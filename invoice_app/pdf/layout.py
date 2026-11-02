"""All PDF geometry, typography and default colours in one place.

Every value was MEASURED from the reference invoice PDF (PyMuPDF: rects, fills,
span bboxes, baselines, fonts) - not guessed. Units are PostScript points; the
Y axis is measured FROM THE TOP of the page (the renderer converts).
Colours here are only defaults; the live colours come from BusinessProfile and
never influence any coordinate.
"""
from reportlab.lib.pagesizes import letter

PAGE_W, PAGE_H = letter            # 612 x 792, portrait

# ---- Fonts (logical name -> preferred TTF file, fallback built-in) -----------
FONT_FILES = {
    'Trebuchet': ('trebuc.ttf', 'Helvetica'),
    'Trebuchet-Bold': ('trebucbd.ttf', 'Helvetica-Bold'),
    'Trebuchet-Italic': ('trebucit.ttf', 'Helvetica-Oblique'),
    'Arial': ('arial.ttf', 'Helvetica'),
    'Arial-Bold': ('arialbd.ttf', 'Helvetica-Bold'),
}

# ---- Default colours ----------------------------------------------------------
DEFAULT_COLORS = {
    'primary': '#0B5394',      # header / badge / total strip
    'alt_row': '#CFE2F3',
    'white_row': '#FFFFFF',
    'border': '#0000FF',
    'label': '#0000FF',        # Bill To:, Invoice Date, Terms, Due Date
    'ink': '#000000',
    'on_primary': '#FFFFFF',
    'rule': '#000000',         # table separators
}

# ---- Running header / page number --------------------------------------------
RUN_HEADER = dict(text='Invoice', x=293.93, baseline=9.12, font='Arial', size=6.96)
PAGE_NUMBER = dict(x=293.93, baseline=785.28, font='Arial', size=6.96, fmt='Page {page}')

# ---- Outer border (filled bars in the sample: 1.32pt thick) -------------------
BORDER = dict(left=50.30, top=47.64, right=554.75, bottom=415.60, width=1.32)

# ---- Company block (top-left) -------------------------------------------------
COMPANY = dict(
    name=dict(x=52.80, baseline=71.52, font='Trebuchet', size=11.06),
    x=52.32,
    lines=[  # baselines of the address lines and contact rows
        dict(baseline=79.10, font='Trebuchet', size=6.96),
        dict(baseline=87.02, font='Trebuchet', size=6.96),
    ],
    contact=dict(x=51.96, baseline=94.22, font='Arial', size=6.96, fmt='Contact Number - {phone}'),
    email=dict(x=52.32, baseline=102.98, font='Arial', size=6.96, fmt='Email ID: {email}'),
    gstin=dict(x=52.56, baseline=111.02, font='Arial', size=6.48, fmt='GSTIN : {gstin}'),
    extra_line_step=7.92,
)

# ---- INVOICE badge (top-right) ------------------------------------------------
BADGE = dict(
    left=496.07, top=56.64, right=554.15, bottom=80.54,
    title=dict(text='INVOICE', x=498.34, baseline=71.52, font='Trebuchet-Bold', size=11.06),
    number=dict(x=498.10, baseline=77.40, font='Trebuchet', size=6.98, fmt='#{number}'),
)

# ---- Bill To ------------------------------------------------------------------
BILL_TO = dict(
    label=dict(text='Bill To:', x=52.44, baseline=150.38, font='Trebuchet-Bold', size=6.96),
    name=dict(x=52.32, baseline=158.78, font='Trebuchet', size=6.96),
    x=52.20,
    first_line_baseline=166.34,
    line_step=7.92,           # 166.34 -> 174.26 -> 182.54 (measured 7.92 / 8.28)
    line_font=('Arial', 6.0),             # street / city lines
    last_line_font=('Trebuchet', 6.96),   # final line (country - zip)
    last_line_extra=0.36,     # final line sits 8.28 below (measured)
    max_lines=3,
)

# ---- Invoice Date | Terms | Due Date -----------------------------------------
DATES = dict(
    label_baseline=198.38, value_baseline=205.34,
    label_font=('Trebuchet-Bold', 6.96), value_font=('Trebuchet', 6.96),
    invoice_date=dict(label_x=52.44, value_x=51.96),
    terms=dict(label_x=295.25, value_center=305.0),   # value centred under "Terms"
    due_date=dict(label_x=428.47, value_x=428.47),
    date_format='%B %d, %Y',
)

# ---- Main table ---------------------------------------------------------------
TABLE = dict(
    left=51.38, right=554.15,
    # inner separator x positions (centre of 0.72pt black bars)
    separators=[226.39, 383.96, 426.92, 496.19],
    rule_width=0.72,
    top=218.05,                 # top black rule centre (217.69-218.41)
    header_top=217.93, header_bottom=237.25,
    header_baseline=231.40,
    header_font=('Trebuchet-Bold', 6.96),
    headers=['Consultant -Project Name', 'Memo/Description', 'Qty ', 'Unit Price', 'Amount'],
    # header text placement: ('left', x) or ('center', None)
    header_align=[('left', 52.44), ('left', 276.65), ('left', 385.51), ('left', 428.47), ('left', 497.74)],
    body_top=237.25,
    body_rows=9,                # configurable default (sample: 9 body rows + total row)
    row_height=8.0822,          # (309.99 - 237.25) / 9
    first_row_shaded=False,     # sample: row 1 white, row 2 light blue, ...
    total_row_height=7.92,      # 309.99 -> 317.91, bounded by black rules
    total_row_shaded=True,
    baseline_offset=7.20,       # from row top (sample text sits low in the row)
    body_font=('Arial', 6.98),
    number_font=('Trebuchet', 6.98),
    text_pad_left=0.94,         # 52.32 - 51.38 ; memo 228.05 - 226.75
    # right edge for right-aligned numbers per numeric column (measured)
    num_right=[425.35, 492.80, 553.32],
    total_font=('Trebuchet', 6.98),
)

# ---- Total strip + INR --------------------------------------------------------
TOTAL_STRIP = dict(
    left=426.80, top=330.63, right=554.14, bottom=339.63,
    separator_x=496.19,
    label=dict(text='Total', x=428.59, baseline=338.21, font='Trebuchet-Bold', size=7.56),
    amount=dict(right=552.73, baseline=338.00, font='Trebuchet', size=6.96),
    currency=dict(right=550.65, baseline=330.53, font='Arial-Bold', size=6.96),
)

# ---- Payment details (bottom-left) -------------------------------------------
PAYMENT = dict(
    title=dict(text='Payment Details', x=52.56, baseline=338.21, font='Trebuchet-Bold', size=7.56),
    x=52.32, font=('Arial', 6.96),
    rows=[
        (347.33, 'Bank Name: {bank_name}'),
        (355.73, 'Account Holder name : {account_holder}'),
        (366.53, 'Account no: {account_number}'),
        (377.33, 'Bank Country Name - {bank_country}'),
        (388.13, 'IFSC code : {ifsc}'),
    ],
)

# ---- Signature (bottom-right) -------------------------------------------------
SIGNATURE = dict(
    image_box=(473.53, 342.14, 535.53, 387.85),   # left, top, right, bottom
    name=dict(center=499.60, baseline=388.13, font='Arial', size=6.96),
)

# ---- Footer -------------------------------------------------------------------
FOOTER = dict(center=303.14, baseline=413.59, font='Trebuchet-Italic', size=6.96)
