/** QC, the factory's quality control: a clipboard with a tick. */
export function QcMark() {
  return (
    <svg className="qc-mark" viewBox="0 0 32 32" aria-hidden="true">
      <rect x="7" y="6" width="18" height="22" rx="3" className="qc-board" />
      <rect x="11.5" y="3.5" width="9" height="5" rx="1.5" className="qc-clip" />
      <path d="M11.5 18l3 3 6-6.5" className="qc-tick" />
    </svg>
  );
}
