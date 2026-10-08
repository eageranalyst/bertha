import { SecurityPanel } from "@/components/SecurityPanel";

export default function SecuritySettingsPage() {
  return (
    <div className="settings-page">
      <p className="skill-hint">
        How well agent containers are contained. Agents run code from the repos they work on, so
        the boundary around them matters more than which commands they&apos;re allowed.
      </p>
      <SecurityPanel />
    </div>
  );
}
