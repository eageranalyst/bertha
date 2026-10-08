import { FactoryMascot } from "@/components/images/FactoryMascot";

export default function SessionsIndex() {
  return (
    <div className="empty-state factory-empty">
      <FactoryMascot />
      Select a session, or start a new one.
    </div>
  );
}
