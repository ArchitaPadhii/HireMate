/* apply.js - shared Apply / Withdraw / Email logic for Browse, Recommended and Job Detail pages */
let ME = {name: '', email: ''};
fetch('/api/me').then(r => r.json()).then(u => { ME = u; });

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function mailLink(j) {
  const subject = 'Application for ' + j.title + ' - ' + j.company;
  const body = 'Hello,\n\nI would like to apply for the ' + j.title + ' position at ' + j.company +
               '.\nMy resume is attached.\n\nRegards,\n' + ME.name + '\n' + ME.email;
  return 'mailto:' + j.contact_email + '?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body);
}

// Buttons for one job card, depending on the application status
function actionButtons(j) {
  let h = '';
  if (j.application_status === 'pending') {
    h += `<button class="btn applied" disabled>Applied \u2713</button>
          <button class="btn withdraw" data-act="withdraw" data-id="${j.id}">Withdraw</button>`;
  } else if (j.application_status === 'accepted') {
    h += `<span class="status accepted">Accepted \u2713</span>`;
  } else if (j.application_status === 'rejected') {
    h += `<span class="status rejected">Rejected</span>`;
  } else {
    h += `<button class="btn" data-act="apply" data-id="${j.id}">Apply</button>`;
  }
  if (j.contact_email) {
    h += `<a class="btn mail" href="${escapeHtml(mailLink(j))}">\u2709 Email recruiter</a>`;
  }
  h += `<a class="btn detail" href="job_detail.html?id=${j.id}">View details</a>`;
  return h;
}

// Make the Apply / Withdraw buttons inside 'root' work. 'reload' is called after success.
function bindActions(root, reload) {
  root.addEventListener('click', e => {
    const b = e.target.closest('button[data-act]');
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    if (act === 'withdraw' && !confirm('Withdraw this application?')) return;
    b.disabled = true;
    fetch(act === 'apply' ? '/api/apply' : '/api/withdraw', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: Number(b.dataset.id)})
    }).then(r => r.json()).then(d => {
      if (d.error) { alert(d.error); b.disabled = false; }
      else { reload(); }
    });
  });
}