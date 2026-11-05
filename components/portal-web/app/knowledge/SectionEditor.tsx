'use client'
import { useActionState, useState } from 'react'
import { saveSectionState, type SaveState } from './actions'

// The editing part of a section. A refused save (someone else saved first, the scan refused it, too long) comes back here as a message,
// and what was typed stays in the box: on a page made for writing, a reload that throws the text away is not acceptable. The box keeps its
// own state because React resets uncontrolled form fields after every form action.
export default function SectionEditor({ team, section, version, content, maxChars, entries, meta }:
  { team: string; section: string; version: number; content: string; maxChars: number; entries: boolean; meta: string }) {
  const [state, action, pending] = useActionState<SaveState, FormData>(saveSectionState, {})
  const [text, setText] = useState(content)
  return (
    <form action={action}>
      <input type="hidden" name="team" value={team} /><input type="hidden" name="section" value={section} />
      <input type="hidden" name="version" value={state.version ?? version} />
      {state.error && <div className="msg flagged">{state.error}</div>}
      <textarea name="content" rows={entries ? 8 : 5} maxLength={maxChars} value={text} onChange={(e) => setText(e.target.value)} style={{ width: '100%' }} placeholder={entries ? 'Name: what it is' : ''} />
      <p className="row" style={{ margin: '6px 0 0', alignItems: 'center' }}>
        <button type="submit" className="primary" disabled={pending}>{pending ? 'Saving…' : 'Save'}</button>
        <small className="soft">{state.saved ? `Saved as v${state.version}. ` : ''}{meta}</small>
      </p>
    </form>
  )
}
