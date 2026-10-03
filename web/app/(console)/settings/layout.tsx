/** The same main and detail-pane frame as the other sections, so the padding matches. */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <main>
      <section id="detail-pane">{children}</section>
    </main>
  );
}
