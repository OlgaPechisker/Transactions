// Mock data. All card numbers are masked: issuer + last 4 only.
window.DB = {
  users: {
    dan: { id: 'dan', name: 'Dan', email: 'dan@example.com' },
    noa: { id: 'noa', name: 'Noa', email: 'noa@example.com' }
  },
  cards: [
    { id: 'c1', owner: 'dan', issuer: 'Max', last4: '4396', nickname: 'Dan – main Max' },
    { id: 'c2', owner: 'dan', issuer: 'Isracard', last4: '1182', nickname: 'Groceries' },
    { id: 'c3', owner: 'dan', issuer: 'Cal', last4: '7710', nickname: '' },
    { id: 'c4', owner: 'noa', issuer: 'Max', last4: '9021', nickname: 'Noa Max' }
  ],
  history: [
    { file: 'max_2026-09.xlsx', hash: '9f3a1c7e', date: '2026-10-02 09:14', status: 'SUCCESS', rows: 87, owner: 'dan' },
    { file: 'isracard_2026-09.xlsx', hash: '41be08d2', date: '2026-10-02 09:20', status: 'PARTIAL', rows: 52, owner: 'dan' },
    { file: 'max_noa_2026-09.xlsx', hash: 'a2e6f410', date: '2026-10-05 21:40', status: 'SUCCESS', rows: 61, owner: 'noa' }
  ],
  // Simulated outcomes of the import CLI
  scenarios: {
    clean: { label: 'Clean file', exit: 'success', read: 87, import: 87, dup: 0, skip: 0, review: 3, warnings: [], issues: [] },
    identity: { label: 'Needs identity', exit: 'identity', read: 64, import: 64, dup: 0, skip: 0, review: 2,
      warnings: ['Card ••••5530 could not be matched to any known card.'], issues: [] },
    partial: { label: 'Partial – row errors', exit: 'partial', read: 52, import: 47, dup: 0, skip: 2, review: 1,
      warnings: ['A sheet has an unrecognised header; 2 rows skipped.'],
      issues: [
        { sheetPosition: 1, row: 14, severity: 'ERROR', message: 'Amount could not be read.' },
        { sheetPosition: 1, row: 33, severity: 'ERROR', message: 'Date could not be read.' },
        { sheetPosition: 2, row: 8, severity: 'WARNING', message: 'Original currency is missing.' },
        { sheetPosition: 2, row: 19, severity: 'INFO', message: 'Merchant is empty.' }
      ] },
    dup: { label: 'Already imported (duplicates)', exit: 'success', read: 87, import: 0, dup: 87, skip: 0, review: 0,
      warnings: ['All rows already exist. Re-importing the same file inserts nothing.'], issues: [] }
  }
};
