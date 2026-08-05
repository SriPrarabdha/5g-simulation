export function Metric({ label, value, unit, tone, detail }: {
  label: string; value: string | number; unit?: string; tone?: string; detail?: string
}) {
  return <div className={`metric ${tone ?? ''}`}>
    <span>{label}</span><div><strong>{value}</strong>{unit && <small>{unit}</small>}</div>{detail && <p>{detail}</p>}
  </div>
}

