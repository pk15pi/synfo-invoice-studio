import re
"""Invoice business logic shared by views, tests and the sample command."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from . import billing, descriptions, gst
from .models import (BusinessProfile, Client, Invoice, InvoiceFormatTemplate, InvoiceLine,
                     ManpowerBillingRecord, ManpowerDeployment, ManpowerRateRevision, ProjectTemplate,
                     RecruitmentPlacement)


@dataclass
class LineInput:
    project: ProjectTemplate | None
    project_name: str
    description: str
    days: Decimal
    from_date: date | None
    to_date: date | None
    unit_price: Decimal | None = Decimal('0.00')
    amount: Decimal = Decimal('0.00')
    amount_override: Decimal | None = None
    unit_override: Decimal | None = None
    sac_code: str = ''
    gst_rate: Decimal = Decimal('0.00')
    deployment: ManpowerDeployment | None = None
    resource_name: str = ''
    designation: str = ''
    billing_method: str = ''
    billing_unit: str = ''
    billing_quantity: Decimal | None = None
    billing_rate: Decimal | None = None
    period_start: date | None = None
    period_end: date | None = None
    line_remarks: str = ''
    placement: RecruitmentPlacement | None = None

    @property
    def amount_overridden(self):
        return self.amount_override is not None


@dataclass
class InvoiceInput:
    client: object
    invoice_date: date
    terms_days: object
    due_date: object
    pdf_number_override: str = ''
    lines: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    terms_mode: str = 'custom'
    terms_text: str = ''
    billing_month: date | None = None
    tax_type: str = 'none'
    tax_rate: Decimal = Decimal('0')
    taxable_override: Decimal | None = None
    cancelled: bool = False
    cancelled_date: date | None = None
    tax_treatment: str = gst.EXPORT
    treatment_posted: bool = False  # False = older form/script: keep the legacy manual tax fields
    place_of_supply: str = ''
    po_number: str = ''
    format_template: object = None   # InvoiceFormatTemplate or None (= default output)
    service_type: str = Invoice.PROJECT_MANPOWER
    resource_display_format: str = 'designation'
    billing_period_start: date | None = None
    billing_period_end: date | None = None
    recruitment_agreement_reference: str = ''
    supplementary: bool = False

    @property
    def total(self):
        return billing.q2(sum((l.amount for l in self.lines), Decimal('0')))


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _dec(value):
    if value in (None, ''):
        return None
    try:
        return billing.to_decimal(value)
    except (InvalidOperation, ValueError):
        return None


def compute_line(line: LineInput, invoice_date, rounding, billing_month=None, service_type=Invoice.PROJECT_MANPOWER,
                 proration_method=None):
    """Server-side (authoritative) calculation for one line."""
    if line.project is not None:
        calc = billing.calculate_for_project(line.project, line.days, period_end=line.to_date,
                                             fallback_date=line.from_date or invoice_date, rounding=rounding)
        line.unit_price, line.amount = calc.unit_price, calc.amount
    elif service_type == Invoice.RECRUITMENT_PLACEMENT:
        if line.placement is None or not line.placement.fee_approved or line.placement.fee_amount is None:
            raise ValueError('Select a client-approved recruitment placement with an approved fee.')
        if line.unit_override is not None or line.amount_override is not None:
            raise ValueError('Recruitment invoice amounts come from the approved placement record; update that record to request an override.')
        line.amount = line.placement.fee_amount
        line.days = Decimal('1')
        line.unit_price = None
        line.resource_name = line.placement.candidate_name_snapshot or line.placement.candidate.candidate_name
        line.designation = line.placement.designation_snapshot or line.placement.designation
        line.billing_method = line.placement.billing_method_snapshot
        if not line.project_name:
            line.project_name = (line.resource_name if line.resource_name else line.designation)
        if not line.description:
            if not line.placement.invoice_display_format == RecruitmentPlacement.DISPLAY_CONSOLIDATED:
                line.description = 'Recruitment and Placement Services as per the agreed commercial terms.'
            else:
                line.description = 'Professional Recruitment Services as per agreed terms.'
        terms = line.placement.agreement_terms_snapshot or {}
        line.sac_code = terms.get('sac_code') or gst.DEFAULT_SAC
        line.gst_rate = billing.q2(terms['gst_rate']) if terms.get('gst_rate') is not None else gst.DEFAULT_RATE
    elif service_type == Invoice.GENERAL_MANPOWER:
        from .manpower_billing import calculate_deployment
        line.unit_price = billing.q2(line.unit_override if line.unit_override is not None else line.billing_rate or 0)
        if line.deployment is not None:
            if not line.period_start or not line.period_end:
                raise ValueError('Choose a billing period before adding a deployment.')
            method, unit, qty, rate, amount, start, end = calculate_deployment(
                line.deployment, line.period_start, line.period_end, rounding,
                billable_quantity=line.billing_quantity, manual_amount=line.amount_override,
                remarks=line.line_remarks)
            line.billing_method, line.billing_unit, line.billing_quantity = method, unit, qty
            line.billing_rate, line.amount, line.from_date, line.to_date = rate, amount, start, end
            line.unit_price = rate
            line.resource_name = line.resource_name or (line.deployment.employee.full_name if line.deployment.employee_id else line.deployment.resource_name)
            line.designation = line.designation or line.deployment.designation
        else:
            method = line.billing_method or ManpowerDeployment.MONTHLY
            quantity = line.billing_quantity if line.billing_quantity is not None else line.days
            rate = line.billing_rate if line.billing_rate is not None else line.unit_override
            if method == ManpowerDeployment.FIXED:
                line.billing_unit, quantity = 'contract', Decimal('1')
                if line.amount_override is None and rate is None:
                    raise ValueError('Enter the fixed contract amount.')
                line.amount = billing.q2(line.amount_override if line.amount_override is not None else rate)
            else:
                if rate is None or rate < 0:
                    raise ValueError('Enter a non-negative client billing rate.')
                if quantity <= 0:
                    raise ValueError('Enter a billable quantity greater than zero.')
                line.billing_unit = 'month' if method == ManpowerDeployment.MONTHLY else ('day' if method == ManpowerDeployment.DAILY else 'hour')
                if method == ManpowerDeployment.MONTHLY and proration_method == ManpowerDeployment.MANUAL:
                    if line.amount_override is None or not line.line_remarks.strip():
                        raise ValueError('Manual proration requires an amount and a reason.')
                    line.amount = billing.q2(line.amount_override)
                else:
                    line.amount = billing.round_amount(rate * quantity, rounding)
                if line.amount_override is not None and method in (ManpowerDeployment.DAILY, ManpowerDeployment.HOURLY):
                    if not line.line_remarks.strip():
                        raise ValueError('A billable day/hour amount adjustment requires a reason.')
                    line.amount = billing.q2(line.amount_override)
            line.billing_method, line.billing_quantity, line.billing_rate = method, quantity, rate
            line.unit_price = rate
            line.days = quantity
        line.sac_code, line.gst_rate = gst.DEFAULT_SAC, gst.DEFAULT_RATE
        if not line.description:
            start = line.period_start or billing_month
            end = line.period_end or billing_month
            if start and end:
                line.description = f'Manpower Supply Services for {start:%d %B %Y} to {end:%d %B %Y} as per agreed terms.'
            elif start:
                line.description = f'Manpower Supply Services for {start:%B %Y} as per agreed terms.'
    else:
        line.unit_price = billing.q2(line.unit_override) if line.unit_override is not None else None
        line.amount = billing.round_amount((line.unit_price or 0) * line.days, rounding)
    if line.project is not None:
        blank_unit = line.project.client.billing_type == billing.MONTHLY_FIXED
    else:
        blank_unit = False
    if line.unit_override is not None and not blank_unit:
        line.unit_price = billing.q2(line.unit_override)
    if line.amount_override is not None:
        line.amount = billing.q2(line.amount_override)
    if line.project is not None:
        line.sac_code = line.project.sac_code or gst.DEFAULT_SAC
        line.gst_rate = billing.q2(line.project.gst_rate if line.project.gst_rate is not None else gst.DEFAULT_RATE)
    elif service_type not in (Invoice.GENERAL_MANPOWER, Invoice.RECRUITMENT_PLACEMENT):  # existing custom service
        line.sac_code, line.gst_rate = gst.DEFAULT_SAC, gst.DEFAULT_RATE
    if not line.description and line.project is not None:
        # Project default (template / custom / blank) via the single rendering service.
        line.description = descriptions.project_default_description(
            line.project, start_date=line.from_date, end_date=line.to_date, billing_month=billing_month)
    return line


def default_terms(client, profile):
    """(mode, text) a new invoice for this customer starts with."""
    if client is not None and client.terms_mode == 'month_days':
        return 'month_days', ''
    if client is not None and client.terms_text:
        return 'custom', client.terms_text
    return 'custom', str(profile.payment_terms_days)


def previous_month(d):
    """First day of the calendar month before ``d``."""
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def parse_month(value):
    """'YYYY-MM' (or an ISO date) -> first day of that month; raises ValueError."""
    v = str(value).strip()
    m = re.fullmatch(r'(\d{4})-(\d{1,2})(?:-\d{1,2})?', v)
    if not m:
        raise ValueError(v)
    return date(int(m.group(1)), int(m.group(2)), 1)


def parse_invoice_post(post, client, profile, invoice=None):
    """Build an InvoiceInput from the invoice form POST (arrays line_*)."""
    errors = []
    invoice_date = _date(post.get('invoice_date')) or date.today()
    mode, text = default_terms(client, profile)
    if post.get('terms_mode') in ('month_days', 'custom'):
        mode = post.get('terms_mode')
    raw = post.get('terms_text')
    if raw is None:
        raw = post.get('terms_days')  # older form field name
    if raw is not None and str(raw).strip() != '':
        text = str(raw).strip()[:40]
    terms_text, terms = billing.resolve_terms(mode, text, invoice_date)
    # Due Date stays editable; auto-filled only when Terms resolves to a whole number.
    due = _date(post.get('due_date')) or (billing.due_date_for(invoice_date, terms) if terms is not None else None)
    data = InvoiceInput(client, invoice_date, terms, due, (post.get('pdf_number_override') or '').strip(),
                        terms_mode=mode, terms_text=terms_text if mode == 'custom' else '')
    # Invoice for (month): missing key (older forms/scripts) -> month before the invoice date; blank -> not set.
    raw_month = post.get('billing_month')
    if 'billing_year' in post or 'billing_mon' in post:  # two-field form: both needed, either blank = not set
        y, m = (post.get('billing_year') or '').strip(), (post.get('billing_mon') or '').strip()
        raw_month = f'{y}-{m}' if (y and m) else ''
    if raw_month is None:
        data.billing_month = previous_month(invoice_date)
    elif str(raw_month).strip():
        try:
            data.billing_month = parse_month(raw_month)
        except ValueError:
            errors.append('Invoice for (month): pick a valid month.')
    get = post.getlist if hasattr(post, 'getlist') else (lambda k: post.get(k, []))
    cols = {k: get(k) for k in ('line_project', 'line_name', 'line_desc', 'line_from', 'line_to',
                                 'line_days', 'line_unit', 'line_amount', 'line_deployment', 'line_method',
                                 'line_proration', 'line_remarks', 'line_placement')}
    n = max((len(v) for v in cols.values()), default=0)
    projects = {str(p.pk): p for p in ProjectTemplate.objects.filter(client=client)} if client else {}
    selected_deployment_periods = []
    selected_placements = set()
    data.service_type = post.get('service_type') if post.get('service_type') in dict(Invoice.SERVICE_TYPES) else Invoice.PROJECT_MANPOWER
    data.resource_display_format = post.get('resource_display_format') if post.get('resource_display_format') in dict(Invoice.RESOURCE_DISPLAY) else profile.manpower_resource_display_default
    data.supplementary = str(post.get('supplementary', '')).lower() in ('1', 'on', 'true')
    if data.billing_month:
        import calendar
        data.billing_period_start = _date(post.get('billing_period_start')) or data.billing_month
        data.billing_period_end = _date(post.get('billing_period_end')) or date(
            data.billing_month.year, data.billing_month.month, calendar.monthrange(data.billing_month.year, data.billing_month.month)[1])
    else:
        data.billing_period_start = _date(post.get('billing_period_start'))
        data.billing_period_end = _date(post.get('billing_period_end'))
    if data.billing_period_start and data.billing_period_end and data.billing_period_end < data.billing_period_start:
        errors.append('Billing period end must be on or after its start date.')
    if data.service_type == Invoice.GENERAL_MANPOWER and (not data.billing_period_start or not data.billing_period_end):
        errors.append('General manpower invoices require a billing month or a custom billing period.')
    for i in range(n):
        val = lambda k: (cols[k][i] if i < len(cols[k]) else '').strip()
        project = projects.get(val('line_project')) if data.service_type == Invoice.PROJECT_MANPOWER else None
        if data.service_type == Invoice.PROJECT_MANPOWER and project is None and val('line_name'):
            # Typed text that exactly matches an active saved project links to it; anything else is custom text.
            typed = val('line_name').casefold()
            project = next((p for p in projects.values() if p.active and p.name.casefold() == typed), None)
        name = val('line_name') or (project.name if project else '')
        days = _dec(val('line_days')) or Decimal('0')
        amount_ov = _dec(val('line_amount'))
        unit_ov = _dec(val('line_unit'))
        deployment = None
        placement = None
        method = val('line_method')
        if data.service_type == Invoice.RECRUITMENT_PLACEMENT:
            if val('line_placement').isdigit():
                placement = RecruitmentPlacement.objects.select_related('candidate', 'agreement', 'invoice').filter(
                    pk=val('line_placement'), client=client, fee_approved=True).first()
            if placement is None:
                errors.append(f'Row {i + 1}: select an approved recruitment placement belonging to this client.')
                continue
            if placement.invoice_id and (invoice is None or placement.invoice_id != invoice.pk):
                errors.append(f'Row {i + 1}: this placement already has an invoice and cannot be billed again.')
                continue
            if placement.pk in selected_placements:
                errors.append(f'Row {i + 1}: this placement is selected more than once.')
                continue
            if amount_ov is not None or unit_ov is not None:
                errors.append(f'Row {i + 1}: edit the placement fee record to request an audited amount override.')
                continue
            selected_placements.add(placement.pk)
            name = placement.candidate_name_snapshot or placement.candidate.candidate_name
            days = Decimal('1')
        if data.service_type == Invoice.GENERAL_MANPOWER and val('line_deployment').isdigit():
            deployment_qs = ManpowerDeployment.objects.select_related('employee', 'project').filter(
                pk=val('line_deployment'), client=client)
            if invoice is None or not invoice.lines.filter(manpower_deployment_id=val('line_deployment')).exists():
                deployment_qs = deployment_qs.filter(status='active')
            deployment = deployment_qs.first()
            if deployment is None:
                errors.append(f'Row {i + 1}: select an active deployment belonging to this client.')
                continue
            method = method or deployment.billing_method
            if not name:
                name = deployment.designation
        if not name and not project:
            continue
        if data.service_type == Invoice.GENERAL_MANPOWER and not method:
            method = ManpowerDeployment.MONTHLY
        if data.service_type == Invoice.GENERAL_MANPOWER and method not in dict(ManpowerDeployment.BILLING_METHODS):
            errors.append(f'Row {i + 1} ({name}): choose a valid billing method.')
            continue
        if data.service_type == Invoice.GENERAL_MANPOWER and amount_ov is not None and method != ManpowerDeployment.FIXED and not val('line_remarks').strip():
            errors.append(f'Row {i + 1} ({name}): a manual amount adjustment requires a reason.')
            continue
        if method == ManpowerDeployment.FIXED:
            days = Decimal('1')
        if deployment and method in (ManpowerDeployment.MONTHLY, ManpowerDeployment.FIXED):
            days = Decimal('1')
        if days <= 0 and amount_ov is None:
            errors.append(f'Row {i + 1} ({name}): enter a billable quantity or an amount.')
            continue
        line = LineInput(project, name, val('line_desc'), days, _date(val('line_from')), _date(val('line_to')),
                         amount_override=amount_ov, unit_override=unit_ov, deployment=deployment,
                         resource_name=(deployment.employee.full_name if deployment and deployment.employee_id else deployment.resource_name if deployment else name),
                         designation=deployment.designation if deployment else name,
                         billing_method=method, billing_quantity=days, billing_rate=unit_ov,
                         period_start=data.billing_period_start, period_end=data.billing_period_end,
                         line_remarks=val('line_remarks'), placement=placement)
        if line.from_date and line.to_date and line.to_date < line.from_date:
            errors.append(f'Row {i + 1} ({name}): "to" date is before "from" date.')
        try:
            computed = compute_line(line, invoice_date, profile.amount_rounding, billing_month=data.billing_month,
                                    service_type=data.service_type, proration_method=val('line_proration'))
        except (ValueError, ValidationError) as exc:
            errors.append(f'Row {i + 1} ({name}): {exc}')
            continue
        if placement:
            if data.resource_display_format == 'name':
                computed.description = f'Recruitment and Placement Services - {placement.candidate_name_snapshot or placement.candidate.candidate_name} - {placement.designation_snapshot or placement.designation}'
            elif data.resource_display_format == 'designation':
                computed.description = f'Recruitment and Placement Services - {placement.designation_snapshot or placement.designation}'
            else:
                computed.description = 'Professional Recruitment Services as per agreed terms.'
        if deployment and data.billing_period_start and data.billing_period_end and not data.supplementary:
            dupe = ManpowerBillingRecord.objects.filter(deployment=deployment,
                period_start__lte=data.billing_period_end, period_end__gte=data.billing_period_start)
            if invoice:
                dupe = dupe.exclude(invoice=invoice)
            repeated_on_form = any(existing_id == deployment.pk and start <= data.billing_period_end and
                                   end >= data.billing_period_start for existing_id, start, end in selected_deployment_periods)
            if dupe.exists() or repeated_on_form:
                errors.append(f'Row {i + 1} ({name}): this deployment already has a bill for the selected period; mark this invoice as supplementary only when approved.')
                continue
        if deployment and data.billing_period_start and data.billing_period_end:
            selected_deployment_periods.append((deployment.pk, data.billing_period_start, data.billing_period_end))
        data.lines.append(computed)
    if not data.lines:
        errors.append('Add at least one invoice line with a billable quantity or amount.')
    if data.service_type == Invoice.RECRUITMENT_PLACEMENT:
        references = sorted({line.placement.agreement_terms_snapshot.get('agreement_reference', '').strip()
                             for line in data.lines if line.placement and line.placement.agreement_terms_snapshot and
                             line.placement.agreement_terms_snapshot.get('agreement_reference', '').strip()})
        data.recruitment_agreement_reference = ', '.join(references)[:300]
    # Tax (optional; absent keys = no tax, which keeps older forms/scripts unchanged)
    data.tax_type = post.get('tax_type') if post.get('tax_type') in ('none', 'cgst_sgst', 'igst') else 'none'
    data.tax_rate = _dec(post.get('tax_rate') or '') or Decimal('0')
    if data.tax_rate < 0 or data.tax_rate > 100:
        errors.append('Tax rate must be between 0 and 100.')
    data.taxable_override = _dec(post.get('taxable_value') or '')
    if data.taxable_override is not None and data.taxable_override < 0:
        errors.append('Taxable value cannot be negative.')
    # GST treatment: posted value, else the customer's default (else export = no tax).
    posted = post.get('tax_treatment')
    data.treatment_posted = posted in gst.TREATMENT_KEYS
    data.tax_treatment = posted if data.treatment_posted else (
        getattr(client, 'tax_treatment', '') or gst.EXPORT)
    if data.tax_treatment not in gst.TREATMENT_KEYS:
        data.tax_treatment = gst.EXPORT
    if data.tax_treatment == gst.DOMESTIC_GST:
        pos = (post.get('place_of_supply') or '').strip() or (getattr(client, 'state_code', '') or '')
        if not pos:
            errors.append('Domestic GST: choose the place of supply (or set the customer state / GSTIN).')
        elif pos not in gst.STATES:
            errors.append(f'Place of supply "{pos}" is not a valid GST state code.')
        data.place_of_supply = pos if pos in gst.STATES else ''
    data.cancelled = str(post.get('cancelled', '')).lower() in ('1', 'on', 'true')
    data.cancelled_date = _date(post.get('cancelled_date')) if data.cancelled else None
    if data.cancelled and not data.cancelled_date:
        from django.utils import timezone
        data.cancelled_date = timezone.localdate()
    data.po_number = (post.get('po_number') or '').strip()[:60]
    if len((post.get('po_number') or '').strip()) > 60:
        errors.append('PO number: at most 60 characters.')
    raw_fmt = (post.get('format_template') or '').strip()
    if raw_fmt:
        data.format_template = InvoiceFormatTemplate.objects.filter(pk=raw_fmt if raw_fmt.isdigit() else 0,
                                                                    is_active=True).first()
        if data.format_template is None:
            errors.append('Invoice format: choose an active format.')
    else:
        data.format_template = getattr(client, 'invoice_format', None) if client else None
    data.errors = errors
    return data


def _write_lines(invoice, lines):
    if invoice.status == Invoice.DRAFT:
        ManpowerBillingRecord.objects.filter(invoice=invoice).delete()
    previous_placement_ids = set(invoice.lines.exclude(recruitment_placement=None).values_list('recruitment_placement_id', flat=True))
    invoice.lines.all().delete()
    display_format = invoice.resource_display_format
    db_lines = [
        InvoiceLine(invoice=invoice, project=l.project, position=i,
                    project_name=((' - '.join(x for x in (l.resource_name, l.designation) if x)
                                   if display_format == 'name' else
                                   l.designation if display_format == 'designation' else
                                   'Professional Recruitment Services')
                                  if invoice.service_type == Invoice.RECRUITMENT_PLACEMENT else
                                  (l.resource_name if display_format == 'name' and l.resource_name else
                                   l.designation if invoice.service_type == Invoice.GENERAL_MANPOWER else l.project_name)),
                    description=l.description, quantity_days=billing.q2(l.days), from_date=l.from_date,
                    description_template_id=(l.project.description_template_id if l.project is not None and
                                             l.project.invoice_description_type == 'template' else None),
                    to_date=l.to_date, unit_price=l.unit_price, amount=l.amount,
                    amount_overridden=l.amount_overridden, sac_code=l.sac_code, gst_rate=l.gst_rate,
                    manpower_deployment=l.deployment, recruitment_placement=l.placement,
                    resource_name_snapshot=l.resource_name,
                    designation_snapshot=l.designation, billing_method_snapshot=l.billing_method,
                    billing_unit_snapshot=l.billing_unit, billing_quantity_snapshot=l.billing_quantity,
                    billing_rate_snapshot=l.billing_rate, billing_period_start=l.from_date or l.period_start,
                    billing_period_end=l.to_date or l.period_end)
        for i, l in enumerate(lines)]
    InvoiceLine.objects.bulk_create(db_lines)
    current_placements = set()
    for line_data, saved_line in zip(lines, db_lines):
        if not line_data.placement:
            continue
        placement = line_data.placement
        current_placements.add(placement.pk)
        placement.invoice = invoice
        placement.invoice_line = saved_line
        placement.save(update_fields=['invoice', 'invoice_line', 'updated_at'])
    if previous_placement_ids - current_placements:
        for placement in RecruitmentPlacement.objects.filter(pk__in=previous_placement_ids - current_placements,
                                                             invoice=invoice):
            placement.invoice = None
            placement.save(update_fields=['invoice', 'updated_at'])
    if invoice.service_type == Invoice.GENERAL_MANPOWER:
        records = []
        for line_data, saved_line in zip(lines, db_lines):
            if not line_data.deployment:
                continue
            start = line_data.period_start or invoice.billing_period_start or invoice.billing_month
            end = line_data.period_end or invoice.billing_period_end or invoice.billing_month
            if start and end:
                records.append(ManpowerBillingRecord(
                    deployment=line_data.deployment, invoice=invoice, invoice_line=saved_line,
                    period_start=start, period_end=end, billing_method=line_data.billing_method,
                    quantity=line_data.billing_quantity or Decimal('1'), rate=line_data.billing_rate or Decimal('0'),
                    amount=line_data.amount, supplementary=invoice.supplementary))
        if records:
            ManpowerBillingRecord.objects.bulk_create(records)


def _keep_line_snapshots(invoice, lines):
    """A non-draft invoice keeps the SAC/GST rate its lines were saved with (by position), so editing a
    project's rate later can never change a finalized invoice's tax."""
    if not invoice.pk or invoice.status == Invoice.DRAFT:
        return
    old = {l.position: l for l in InvoiceLine.objects.filter(invoice=invoice)}
    for i, l in enumerate(lines):
        if i in old:
            l.sac_code, l.gst_rate = old[i].sac_code, old[i].gst_rate


def save_invoice(data: InvoiceInput, invoice: Invoice | None = None) -> Invoice:
    """Create (consuming exactly ONE sequence number) or update an invoice.

    The number is taken under ``select_for_update`` inside ``transaction.atomic`` so
    concurrent saves cannot reuse or skip numbers. Editing never consumes a number,
    and ``pdf_number_override`` never touches ``invoice_number`` or the sequence."""
    with transaction.atomic():
        can_change_invoice_terms = invoice is None or invoice.status == Invoice.DRAFT
        if invoice is not None and invoice.status != Invoice.DRAFT and invoice.service_type in (
                Invoice.GENERAL_MANPOWER, Invoice.RECRUITMENT_PLACEMENT):
            raise ValidationError('A finalized manpower or recruitment invoice cannot be edited. Issue a credit note or cancellation through the existing invoice workflow.')
        if data.service_type == Invoice.GENERAL_MANPOWER and not data.supplementary:
            for line in data.lines:
                if not line.deployment or not line.period_start or not line.period_end:
                    continue
                duplicates = ManpowerBillingRecord.objects.filter(
                    deployment=line.deployment, period_start__lte=line.period_end, period_end__gte=line.period_start)
                if invoice and invoice.pk:
                    duplicates = duplicates.exclude(invoice=invoice)
                if duplicates.exists():
                    raise ValidationError(f'{line.designation or line.project_name} already has a bill that overlaps this period.')
        if data.service_type == Invoice.RECRUITMENT_PLACEMENT:
            for line in data.lines:
                if not line.placement:
                    continue
                locked_placement = RecruitmentPlacement.objects.select_for_update().get(pk=line.placement.pk)
                if not locked_placement.fee_approved or locked_placement.fee_amount != line.placement.fee_amount:
                    raise ValidationError('The approved placement fee changed. Refresh the invoice and review the placement again.')
                if locked_placement.invoice_id and (invoice is None or locked_placement.invoice_id != invoice.pk):
                    raise ValidationError('This placement already has an invoice.')
        if invoice is None:
            BusinessProfile.current()
            profile = BusinessProfile.objects.select_for_update().get(pk=1)
            number = profile.next_number()
            while Invoice.objects.filter(invoice_number=number).exists():  # never collide
                profile.next_invoice_number += 1
                number = profile.next_number()
            profile.next_invoice_number += 1
            profile.save(update_fields=['next_invoice_number'])
            invoice = Invoice(invoice_number=number)
        invoice.client = data.client
        invoice.invoice_date = data.invoice_date
        invoice.terms_days = data.terms_days
        invoice.terms_mode = data.terms_mode
        invoice.terms_text = data.terms_text
        invoice.due_date = data.due_date
        invoice.pdf_number_override = data.pdf_number_override
        invoice.billing_month = data.billing_month
        if can_change_invoice_terms:
            invoice.service_type = data.service_type
            invoice.resource_display_format = data.resource_display_format
            invoice.billing_period_start = data.billing_period_start
            invoice.billing_period_end = data.billing_period_end
            invoice.recruitment_agreement_reference = data.recruitment_agreement_reference
            invoice.supplementary = data.supplementary
        invoice.total = data.total
        _keep_line_snapshots(invoice, data.lines)
        apply_format_input(invoice, data)
        apply_tax_input(invoice, data)
        invoice.save()
        _write_lines(invoice, data.lines)
        if data.pdf_number_override:
            Client.objects.filter(pk=data.client.pk).update(last_pdf_ref_no=data.pdf_number_override)
    return invoice


def apply_format_input(invoice, data):
    """PO number + PDF format. A draft/new invoice takes the chosen format and a fresh snapshot of its
    settings; a finalized or cancelled invoice keeps the format snapshot it was saved with."""
    if invoice.pk and invoice.status != Invoice.DRAFT:
        return
    invoice.po_number = data.po_number
    invoice.format_template = data.format_template
    from .pdf.formats import snapshot_for
    invoice.format_snapshot = snapshot_for(data.format_template, data.client)


def apply_tax_input(invoice, data):
    """Fill the invoice's tax fields (the ledger's source of truth) from the input.

    * Domestic GST: computed per line by gst.apply_to_invoice (CGST+SGST or IGST from place of supply).
    * Export / Export under LUT: no tax (taxable = line total or the explicit taxable override).
    * Export keeps the legacy manual tax_type/tax_rate inputs (absent = no tax), so older scripts and
      invoices saved with manual tax behave exactly as before."""
    profile = BusinessProfile.current()
    if invoice.pk and invoice.status != Invoice.DRAFT:
        # Finalized/cancelled: GST is never recomputed; stored tax figures stay exactly as posted.
        invoice.cancelled, invoice.cancelled_date = data.cancelled, data.cancelled_date
        return
    invoice.tax_treatment = data.tax_treatment
    invoice.place_of_supply = data.place_of_supply if data.tax_treatment == gst.DOMESTIC_GST else ''
    invoice.cancelled, invoice.cancelled_date = data.cancelled, data.cancelled_date
    if data.tax_treatment == gst.DOMESTIC_GST:
        gst.apply_to_invoice(invoice, [(l.amount, l.gst_rate) for l in data.lines], profile)
        return
    invoice.taxable_value = data.taxable_override if data.taxable_override is not None else data.total
    if data.tax_treatment == gst.EXPORT_LUT:
        invoice.tax_type, invoice.tax_rate = Invoice.TAX_NONE, Decimal('0')
    else:
        invoice.tax_type, invoice.tax_rate = data.tax_type, data.tax_rate
    invoice.apply_tax(profile.amount_rounding)


def unsaved_invoice(data: InvoiceInput, number: str):
    """In-memory Invoice + lines for PDF preview (no DB writes, no number consumed)."""
    inv = Invoice(client=data.client, invoice_number=number, pdf_number_override=data.pdf_number_override,
                  invoice_date=data.invoice_date, terms_days=data.terms_days, due_date=data.due_date,
                  terms_mode=data.terms_mode, terms_text=data.terms_text, billing_month=data.billing_month,
                  total=data.total, service_type=data.service_type,
                  resource_display_format=data.resource_display_format,
                  billing_period_start=data.billing_period_start, billing_period_end=data.billing_period_end,
                  recruitment_agreement_reference=data.recruitment_agreement_reference,
                  supplementary=data.supplementary)
    apply_format_input(inv, data)
    apply_tax_input(inv, data)
    lines = [InvoiceLine(project_name=((' - '.join(x for x in (l.resource_name, l.designation) if x)
                                       if data.resource_display_format == 'name' else
                                       l.designation if data.resource_display_format == 'designation' else
                                       'Professional Recruitment Services')
                                      if data.service_type == Invoice.RECRUITMENT_PLACEMENT else
                                      (l.resource_name if data.resource_display_format == 'name' and l.resource_name else
                                       l.designation if data.service_type == Invoice.GENERAL_MANPOWER else l.project_name)),
                         description=l.description, quantity_days=billing.q2(l.days),
                         from_date=l.from_date, to_date=l.to_date, unit_price=l.unit_price, amount=l.amount,
                         sac_code=l.sac_code, gst_rate=l.gst_rate,
                         manpower_deployment=l.deployment, recruitment_placement=l.placement,
                         resource_name_snapshot=l.resource_name,
                         designation_snapshot=l.designation, billing_method_snapshot=l.billing_method,
                         billing_unit_snapshot=l.billing_unit, billing_quantity_snapshot=l.billing_quantity,
                         billing_rate_snapshot=l.billing_rate, billing_period_start=l.from_date or l.period_start,
                         billing_period_end=l.to_date or l.period_end)
             for l in data.lines]
    return inv, lines


def column_overflow_warnings(lines):
    """Warn when a value is wider than its PDF cell (it would get an ellipsis)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from .pdf import layout as L
    from .pdf.fonts import font
    t = L.TABLE
    edges = [t['left']] + t['separators'] + [t['right']]
    f, s = t['body_font']
    out = []
    for l in lines:
        for text, i in ((l.project_name, 0), (l.description, 1)):
            if stringWidth(text or '', font(f), s) > edges[i + 1] - edges[i] - 2 * t['text_pad_left']:
                out.append(f'"{text}" is too long for the PDF column and will be shortened.')
    return out
