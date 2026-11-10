"""Forgot-password wizard: identify the employee, choose an email, verify the code."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from . import password_reset as reset
from .forms import ResetChannelForm, ResetIdentifierForm, ResetPasswordForm
from .models import PasswordResetOTP


@never_cache
def identify(request):
    if request.method == 'GET':
        reset.clear_session(request)
        return render(request, 'access_control/password_reset_identify.html',
                      {'form': ResetIdentifierForm()})
    form = ResetIdentifierForm(request.POST)
    if form.is_valid():
        user, _employee = reset.active_employee_account(form.cleaned_data['identifier'])
        if user is None:
            form.add_error('identifier', reset.GENERIC_ERROR)
        else:
            request.session[reset.SESSION_USER] = user.pk
            request.session[reset.SESSION_IDENTIFIER] = form.cleaned_data['identifier']
            request.session.pop(reset.SESSION_CHANNEL, None)
            return redirect('access_control:password_reset_choose')
    return render(request, 'access_control/password_reset_identify.html', {'form': form})


@never_cache
def choose(request):
    user, employee = reset.session_account(request)
    if user is None:
        reset.clear_session(request)
        return redirect('access_control:password_reset')
    channels = reset.reset_channels(employee)
    if not channels:
        reset.clear_session(request)
        messages.error(request, 'No work or personal email is on file for your employee record.')
        return redirect('access_control:password_reset')
    form = ResetChannelForm(request.POST or None, channels=channels)
    if request.method == 'POST' and form.is_valid():
        site_url = request.build_absolute_uri(reverse('access_control:password_reset_verify'))
        otp, error = reset.issue_code(user, employee, form.cleaned_data['channel'], site_url=site_url)
        if error:
            form.add_error(None, error)
        else:
            request.session[reset.SESSION_CHANNEL] = otp.channel
            return redirect('access_control:password_reset_verify')
    return render(request, 'access_control/password_reset_choose.html', {
        'form': form,
        'channels': channels,
    })


@never_cache
def verify(request):
    user, employee = reset.session_account(request)
    if user is None:
        reset.clear_session(request)
        return redirect('access_control:password_reset')
    channels = reset.reset_channels(employee)
    channel = request.session.get(reset.SESSION_CHANNEL)
    channel_data = next((item for item in channels if item['code'] == channel), None)
    if channel_data is None:
        return redirect('access_control:password_reset_choose')

    if request.method == 'POST' and request.POST.get('action') == 'resend':
        site_url = request.build_absolute_uri(reverse('access_control:password_reset_verify'))
        otp, error = reset.issue_code(user, employee, channel, site_url=site_url)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, f'A new code was sent to {reset.mask_email(otp.email)}.')
        return redirect('access_control:password_reset_verify')

    form = ResetPasswordForm(request.POST or None, user=user)
    if request.method == 'POST' and form.is_valid():
        otp, status = reset.verify_code(user, channel, form.cleaned_data['code'])
        if status == 'ok':
            user.set_password(form.cleaned_data['password'])
            user.save(update_fields=['password'])
            reset.consume_code(otp)
            reset.clear_session(request)
            messages.success(request, 'Your password was reset. Sign in with the new password.')
            return redirect('access_control:login')
        if status == 'expired':
            form.add_error('code', 'That code has expired. Request a new code.')
        elif status in ('locked', 'missing'):
            reset.clear_session(request)
            messages.error(request, 'That code is no longer valid. Start again to receive a new code.')
            return redirect('access_control:password_reset')
        else:
            form.add_error('code', 'That code is not correct. Check the email and try again.')

    latest = (PasswordResetOTP.objects.filter(user=user, channel=channel)
              .order_by('-created_at').first())
    if latest is None:
        return redirect('access_control:password_reset_choose')
    return render(request, 'access_control/password_reset_verify.html', {
        'form': form,
        'channel_label': channel_data['label'],
        'masked_email': reset.mask_email(latest.email),
    })
