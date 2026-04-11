export interface SetupStatus {
  first_run: boolean;
  redirect_to?: string;
}

export interface OnboardingStatus {
  providers_complete: boolean;
  sleep_complete: boolean;
  assistant_identity_complete: boolean;
  user_profile_complete: boolean;
  onboarding_complete: boolean;
  next_step: string;
  redirect_to?: string | null;
}

export async function resolveOnboardingRedirect(
  apiGet: <T>(path: string) => Promise<T>,
): Promise<string | null> {
  const setup = await apiGet<SetupStatus>('/api/setup-status');
  if (setup.first_run) {
    return setup.redirect_to || '/providers?wizard=1';
  }

  const onboarding = await apiGet<OnboardingStatus>('/api/onboarding/status');
  if (!onboarding.onboarding_complete) {
    return onboarding.redirect_to || null;
  }

  return null;
}