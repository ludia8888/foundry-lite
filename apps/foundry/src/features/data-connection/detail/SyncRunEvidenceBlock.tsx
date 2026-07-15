interface SyncRunEvidenceBlockProps {
  title: string;
  value: Record<string, unknown>;
}

export function SyncRunEvidenceBlock({ title, value }: SyncRunEvidenceBlockProps) {
  return (
    <div>
      <div className="section-label mb-1">{title}</div>
      <pre className="max-h-28 overflow-auto rounded bg-muted/60 p-2 font-mono text-[10px] leading-4">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
