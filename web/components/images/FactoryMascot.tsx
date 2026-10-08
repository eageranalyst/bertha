/** A little factory machine dozing on its conveyor belt, for the empty sessions pane. */
export function FactoryMascot() {
  return (
    <svg className="factory-mascot" viewBox="0 0 160 120" aria-hidden="true">
      <ellipse cx="80" cy="112" rx="60" ry="4" className="factory-mascot-shadow" />
      <circle cx="98" cy="12" r="4" className="factory-mascot-puff" />
      <circle cx="107" cy="5" r="2.5" className="factory-mascot-puff factory-mascot-puff-late" />
      <rect x="90" y="20" width="14" height="24" rx="2" className="factory-mascot-part" />
      <path d="M88 20 h18" className="factory-mascot-part" />
      <rect x="54" y="32" width="12" height="8" rx="3" className="factory-mascot-lamp" />
      <rect x="40" y="40" width="76" height="52" rx="10" className="factory-mascot-body" />
      <path d="M58 62 q4 3 8 0" className="factory-mascot-face" />
      <path d="M90 62 q4 3 8 0" className="factory-mascot-face" />
      <path d="M74 72 q4 3 8 0" className="factory-mascot-face" />
      <circle cx="56" cy="71" r="3" className="factory-mascot-blush" />
      <circle cx="100" cy="71" r="3" className="factory-mascot-blush" />
      <rect x="14" y="92" width="132" height="12" rx="6" className="factory-mascot-part" />
      <circle cx="24" cy="98" r="2.5" className="factory-mascot-part" />
      <circle cx="80" cy="98" r="2.5" className="factory-mascot-part" />
      <circle cx="136" cy="98" r="2.5" className="factory-mascot-part" />
      <rect x="122" y="78" width="14" height="14" rx="2" className="factory-mascot-crate" />
      <path d="M122 85 h14" className="factory-mascot-crate" />
    </svg>
  );
}

/** The same machine without its belt, sized for the header logo tile. */
export function FactoryLogo() {
  return (
    <svg className="factory-logo" viewBox="0 0 32 32" aria-hidden="true">
      <rect x="19" y="4" width="5" height="8" rx="1" className="factory-logo-part" />
      <rect x="8" y="7" width="5" height="3" rx="1.5" className="factory-logo-lamp" />
      <rect x="4" y="10" width="24" height="17" rx="4" className="factory-logo-body" />
      <path d="M9.5 18 q1.5 1.2 3 0" className="factory-logo-face" />
      <path d="M19.5 18 q1.5 1.2 3 0" className="factory-logo-face" />
      <path d="M14.5 22 q1.5 1.2 3 0" className="factory-logo-face" />
    </svg>
  );
}
