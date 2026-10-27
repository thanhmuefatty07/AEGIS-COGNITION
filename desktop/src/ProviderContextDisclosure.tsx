type ProviderContextDisclosureProps = {
  active: boolean;
  endpoint: string | null;
  placement: "composer" | "setup";
  providerLabel: string;
};

export function providerDestinationHost(endpoint: string | null): string | null {
  if (!endpoint) return null;
  try {
    return new URL(endpoint).host || null;
  } catch {
    return null;
  }
}

function destinationLabel(providerLabel: string, endpoint: string | null): string {
  if (!endpoint) return `${providerLabel} (destination not entered yet)`;
  const host = providerDestinationHost(endpoint);
  return host ? `${providerLabel} (${host})` : `${providerLabel} (destination host unavailable)`;
}

export default function ProviderContextDisclosure({
  active,
  endpoint,
  placement,
  providerLabel,
}: ProviderContextDisclosureProps) {
  if (!active) return null;

  return (
    <p
      className={`provider-context-disclosure provider-context-disclosure--${placement}`}
      role="note"
      aria-label="Live provider data notice"
    >
      <strong>Live provider data notice.</strong>{" "}
      When you send in Live mode, your message and any supported images you attach go to{" "}
      {destinationLabel(providerLabel, endpoint)}. AEGIS may include profile instructions and enabled-skill
      instructions, recent conversation turns, saved-memory/session excerpts, and workspace paths and metadata. If
      tool calling is enabled, the provider may receive tool descriptions and results; results can include source
      excerpts or other local data. The exact context varies; not every category is sent every time. Credential
      redaction is incomplete, so sensitive text may still be sent.
    </p>
  );
}
