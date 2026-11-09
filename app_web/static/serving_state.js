/* A model's serving state as the hub derives it (`serving_state` on each
 * /admin/api/models row: stopped, loading, ready, adopted, failed, unknown),
 * read the same way by the Models tab's row, chips, verb and headline. */

/* A peer on an older build sends only `ownership`, read the way it always was. */
export function servingState(m) {
  if (m.serving_state) return m.serving_state;
  if (m.ownership === 'ours') return 'ready';
  return m.ownership === 'external' ? 'adopted' : 'stopped';
}

/* Serves requests: ready or adopted. Loading, failed and unknown do not. */
export function isServing(m) {
  const s = servingState(m);
  return s === 'ready' || s === 'adopted';
}

/* The row's chip for a state that needs attention, else null. */
export function servingChip(m) {
  const s = servingState(m);
  if (s === 'failed') return { text: 'failed', tone: 'danger' };
  if (s === 'unknown') return { text: 'unknown', tone: 'attention' };
  if (s === 'loading') return { text: 'loading' };
  return null;
}

/* The word an on-demand row's startup label shows for this state, if any. */
const ON_DEMAND_WORD = { loading: 'loading', failed: 'failed', unknown: 'unknown' };

export function onDemandWord(m) {
  return ON_DEMAND_WORD[servingState(m)] || null;
}
