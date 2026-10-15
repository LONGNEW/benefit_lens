/** In-memory manual editing only. Re-entering edit mode never resets user text. */
export interface DraftTextSection {
  question_id: string;
  text: string;
}
export interface DraftEditState {
  editing: boolean;
  texts: Record<string, string>;
}
export type DraftEditAction =
  | { type: "enter"; sections: ReadonlyArray<DraftTextSection> }
  | { type: "edit"; questionId: string; text: string }
  | { type: "restore"; sections: ReadonlyArray<DraftTextSection> }
  | { type: "reset" };

export function initialDraftEditState(): DraftEditState {
  return { editing: false, texts: {} };
}

function fromSections(sections: ReadonlyArray<DraftTextSection>): Record<string, string> {
  return Object.fromEntries(sections.map(section => [section.question_id, section.text]));
}

export function draftEditReducer(state: DraftEditState, action: DraftEditAction): DraftEditState {
  switch (action.type) {
    case "enter":
      return state.editing ? state : { editing: true, texts: fromSections(action.sections) };
    case "edit":
      if (!state.editing || !Object.prototype.hasOwnProperty.call(state.texts, action.questionId)) return state;
      return { editing: true, texts: { ...state.texts, [action.questionId]: action.text } };
    case "restore":
      return state.editing ? { editing: true, texts: fromSections(action.sections) } : state;
    case "reset":
      return initialDraftEditState();
  }
}
