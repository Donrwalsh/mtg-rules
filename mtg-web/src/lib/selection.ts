// The citation the reader is looking at. `occurrence` is the Piece.index of
// the marker they clicked, so the sentence highlight follows that marker when
// the same source is cited more than once; null when chosen from the sheet.
export interface Selection {
  number: number;
  occurrence: number | null;
}
