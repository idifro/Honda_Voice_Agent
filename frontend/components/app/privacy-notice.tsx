// Shown before a call (SPEC §17, India's DPDP Act): what is collected, why, who processes it.
export function PrivacyNotice({ className }: { className?: string }) {
  return (
    <div className={className}>
      <p className="text-foreground font-semibold">Privacy notice (demo)</p>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-left">
        <li>
          Your voice is processed live to understand you and reply: speech recognition and speech by
          Sarvam AI, replies by Azure OpenAI, audio carried by LiveKit Cloud. Calls are{' '}
          <strong>not recorded</strong>.
        </li>
        <li>
          If you book a test ride, we store your name, mobile number, chosen slot, call language and
          your answer about a driving licence, only to arrange the ride. A copy of the booking (with
          the number masked) goes to the showroom&apos;s Google Calendar.
        </li>
        <li>
          To help you choose, the assistant notes what you&apos;re looking for (for example use,
          budget, timeline) as categories, never your words, and keeps a short summary of the call.
        </li>
        <li>
          The showroom calls you back only if you agree when the assistant reads your number back.
          We then store your name, number, language and that consent (with its time), only for that
          callback.
        </li>
        <li>
          This is a demo: please don&apos;t share anything you wouldn&apos;t want a showroom to see.
        </li>
      </ul>
    </div>
  );
}
