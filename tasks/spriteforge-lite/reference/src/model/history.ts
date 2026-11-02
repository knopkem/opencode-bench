import type { DocState } from "./document";

/** Unlimited undo/redo using full state snapshots. */
export class History {
  private undoStack: DocState[] = [];
  private redoStack: DocState[] = [];

  /** Push the state that was current *before* an edit. */
  push(prev: DocState): void {
    this.undoStack.push(structuredClone(prev));
    this.redoStack.length = 0;
  }

  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  undoCount(): number {
    return this.undoStack.length;
  }

  redoCount(): number {
    return this.redoStack.length;
  }

  /** Returns the previous state, pushing `current` onto redo. */
  undo(current: DocState): DocState | null {
    const prev = this.undoStack.pop();
    if (!prev) return null;
    this.redoStack.push(structuredClone(current));
    return structuredClone(prev);
  }

  /** Returns the next state, pushing `current` back onto undo. */
  redo(current: DocState): DocState | null {
    const next = this.redoStack.pop();
    if (!next) return null;
    this.undoStack.push(structuredClone(current));
    return structuredClone(next);
  }

  clear(): void {
    this.undoStack.length = 0;
    this.redoStack.length = 0;
  }
}
