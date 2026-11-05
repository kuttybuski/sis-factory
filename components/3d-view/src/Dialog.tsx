import { useEffect, useRef, type ReactNode } from 'react'

/** A confirmation in the dashboard's style instead of the browser's pop-up. Focus starts on Cancel; Esc (handled by the app) and a click
 * outside cancel too. */
export default function Dialog({ title, children, confirm, danger, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirm: string; danger?: boolean; onConfirm: () => void; onCancel: () => void
}) {
  const cancel = useRef<HTMLButtonElement>(null)
  useEffect(() => { cancel.current?.focus() }, [])
  return (
    <div className="overlay" onClick={onCancel}>
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="dialog-title">{title}</h2>
        <div className="dialog-body">{children}</div>
        <div className="dialog-actions">
          <button ref={cancel} className="btn link" onClick={onCancel}>Cancel</button>
          <button className={`btn primary${danger ? ' danger' : ''}`} onClick={onConfirm}>{confirm}</button>
        </div>
      </div>
    </div>
  )
}
