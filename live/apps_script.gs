// Price Targets live: starts the GitHub live job every 30 minutes while the New York market is open, and outside
// market hours only when a new fill is waiting on the fills tab. GitHub's own schedule skips most runs, so this
// time trigger replaces it. Setup: Script property GITHUB_TOKEN = a fine-grained GitHub token for
// kiliantaylor/portfolio-management-settings with "Actions: Read and write"; then run setup() once.
const WORKFLOW = 'https://api.github.com/repos/kiliantaylor/portfolio-management-settings/actions/workflows/live.yml/dispatches';

function marketOpen_(d) {
  const ny = Utilities.formatDate(d, 'America/New_York', 'u HH:mm').split(' ');  // u = day of week, 1 = Monday
  return +ny[0] <= 5 && ny[1] >= '09:25' && ny[1] <= '16:20';
}

function newFillWaiting_() {
  const sh = SpreadsheetApp.getActive().getSheetByName('fills');
  const n = sh.getLastRow();
  if (n < 2) return false;
  return sh.getRange(2, 10, n - 1, 1).getValues().some(r => String(r[0]) === 'new');
}

function runLive() {
  if (!marketOpen_(new Date()) && !newFillWaiting_()) return;
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  const res = UrlFetchApp.fetch(WORKFLOW, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main' }) });
  if (res.getResponseCode() !== 204) throw new Error('GitHub ' + res.getResponseCode() + ': ' + res.getContentText());
}

function setup() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('runLive').timeBased().everyMinutes(30).create();
  runLive();
}
