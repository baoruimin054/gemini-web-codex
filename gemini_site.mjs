export const geminiApp = 'https://gemini.google.com/app';

export async function geminiProfile() {
  const installed = await profiles();
  return installed.find(profile => profile.id === 'Profile 1') ??
    installed.find(profile => profile.isDefault) ?? installed[0];
}

export function geminiPageStatus(requireEditor=true) {
  if (/^(?:www\.)?google\.com$/.test(location.hostname) && /^\/sorry(?:\/|$)/.test(location.pathname))
    return 'verification_required';
  if (location.hostname === 'accounts.google.com')
    return /\/(?:challenge|verification)(?:\/|$)/i.test(location.pathname)
      ? 'verification_required' : 'login_required';
  if (location.hostname !== 'gemini.google.com') return null;
  if (/isn.t currently supported in your country|您所在的国家或地区暂不支持/i.test(document.body?.innerText ?? ''))
    return 'region_unavailable';
  const account = document.querySelector('a[href^="https://accounts.google.com/SignOutOptions"], [aria-label^="Google Account"], [aria-label^="Google 帐号"], [aria-label^="Google 账号"]');
  const editor = document.querySelector('[contenteditable=true][role=textbox], rich-textarea [contenteditable=true]');
  if (account && (editor || !requireEditor)) return 'ready';
  if (!account && document.querySelector('a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/v3/signin"], a[href*="accounts.google.com/signin"]'))
    return 'login_required';
  return null;
}
