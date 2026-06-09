// Inline SVG sprite — loaded once, reused everywhere
(function(){
  const s = document.createElement('div');
  s.style.display = 'none';
  s.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg">
  <symbol id="i-shield"     viewBox="0 0 24 24"><path d="M12 2L3 7v5c0 5.25 3.75 10.15 9 11.35C17.25 22.15 21 17.25 21 12V7z"/></symbol>
  <symbol id="i-user"       viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></symbol>
  <symbol id="i-monitor"    viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></symbol>
  <symbol id="i-settings"   viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></symbol>
  <symbol id="i-headphones" viewBox="0 0 24 24"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></symbol>
  <symbol id="i-cpu" viewBox="0 0 24 24"><rect x="9" y="9" width="6" height="6"/><rect x="2" y="2" width="20" height="20" rx="2"/><line x1="9" y1="2" x2="9" y2="5"/><line x1="15" y1="2" x2="15" y2="5"/><line x1="9" y1="19" x2="9" y2="22"/><line x1="15" y1="19" x2="15" y2="22"/><line x1="2" y1="9" x2="5" y2="9"/><line x1="2" y1="15" x2="5" y2="15"/><line x1="19" y1="9" x2="22" y2="9"/><line x1="19" y1="15" x2="22" y2="15"/></symbol>
  <symbol id="i-file-text" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></symbol>
  <symbol id="i-arrow-right" viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></symbol>
  <symbol id="i-arrow-left"  viewBox="0 0 24 24"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></symbol>
  <symbol id="i-zap"    viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></symbol>
  <symbol id="i-phone"  viewBox="0 0 24 24"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.61 3.4 2 2 0 0 1 3.6 1.22h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.8a16 16 0 0 0 6 6l.95-.96a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 21.62 16l.3.92z"/></symbol>
  <symbol id="i-send"   viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></symbol>
  <symbol id="i-check"  viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></symbol>
  <symbol id="i-ticket" viewBox="0 0 24 24"><path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v2z"/></symbol>
  <symbol id="i-plus"   viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></symbol>
  <symbol id="i-home"   viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></symbol>
  <symbol id="i-chevron-right" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></symbol>
  <symbol id="i-chevron-down"  viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></symbol>
  <symbol id="i-clock"  viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></symbol>
  <symbol id="i-search"     viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></symbol>
  <symbol id="i-upload"     viewBox="0 0 24 24"><polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/></symbol>
  <symbol id="i-paperclip"  viewBox="0 0 24 24"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></symbol>
  <symbol id="i-inbox"      viewBox="0 0 24 24"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></symbol>
  <symbol id="i-alert"      viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></symbol>
  <symbol id="i-wifi" viewBox="0 0 24 24"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/></symbol>
  <symbol id="i-key" viewBox="0 0 24 24"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></symbol>
  <symbol id="i-shopping-cart" viewBox="0 0 24 24"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></symbol>
  <symbol id="i-repeat" viewBox="0 0 24 24"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></symbol>
  <symbol id="i-code" viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></symbol>
  <symbol id="i-external-link" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></symbol>
  <symbol id="i-eye" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><polyline points="8 17 12 21 16 17"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.88 18.09A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.29"/></symbol>
  </svg>`;
  document.body.prepend(s);
})();

// Generate ref number on form pages
(function(){
  const el = document.getElementById('ref-val');
  if (!el) return;
  const d = new Date();
  el.textContent = 'DRAFT-' + d.getFullYear() +
    String(d.getMonth()+1).padStart(2,'0') +
    String(d.getDate()).padStart(2,'0');
})();

// Simple form validation
(function(){
  const form = document.getElementById('request-form');
  if (!form) return;
  form.addEventListener('submit', function(e){
    const empId = form.querySelector('[name="emp_id"]');
    const phone = form.querySelector('[name="phone"]');
    const subject = form.querySelector('[name="subject"]');
    let ok = true;

    [empId, phone, subject].forEach(function(f){
      if (!f) return;
      if (!f.value.trim()){
        f.style.borderColor = '#c0392b';
        f.style.boxShadow = '0 0 0 4px rgba(192,57,43,.1)';
        ok = false;
        f.addEventListener('input', function(){
          f.style.borderColor = '';
          f.style.boxShadow = '';
        }, {once:true});
      }
    });

    // ✅ เพิ่มส่วนนี้ — บังคับเลือกบริษัท
    const companyRadios = form.querySelectorAll('[name="company"]');
    if (companyRadios.length > 0) {
      const companyChecked = form.querySelector('[name="company"]:checked');
      if (!companyChecked) {
        // หา div ที่ wrap radio buttons แล้ว highlight
        const companyGroup = companyRadios[0].closest('.f-1col');
        if (companyGroup) {
          companyGroup.style.outline = '2px solid #c0392b';
          companyGroup.style.borderRadius = '6px';
          companyGroup.style.padding = '6px';
          // scroll ไปหา
          companyGroup.scrollIntoView({ behavior: 'smooth', block: 'center' });
          // เมื่อเลือกแล้วให้เคลียร์ highlight
          companyRadios.forEach(function(r){
            r.addEventListener('change', function(){
              companyGroup.style.outline = '';
              companyGroup.style.padding = '';
            }, {once: true});
          });
        }
        ok = false;
      }
    }

    if (!ok) e.preventDefault();
  });
})();


// ── File drag-drop ──────────────────────────────────────────
(function(){
  const drop    = document.getElementById('file-drop');
  const input   = document.getElementById('file-input');
  const preview = document.getElementById('file-preview');
  const nameEl  = document.getElementById('file-name');
  const remove  = document.getElementById('file-remove');
  if (!drop || !input) return;

  function showFile(file) {
    nameEl.textContent = file.name;
    preview.style.display = 'flex';
    drop.style.display = 'none';
  }
  function clearFile() {
    input.value = '';
    preview.style.display = 'none';
    drop.style.display = 'block';
  }

  input.addEventListener('change', function() {
    if (input.files[0]) showFile(input.files[0]);
  });
  remove.addEventListener('click', clearFile);

  drop.addEventListener('dragover',  function(e){ e.preventDefault(); drop.classList.add('drag-over'); });
  drop.addEventListener('dragleave', function()  { drop.classList.remove('drag-over'); });
  drop.addEventListener('drop', function(e){
    e.preventDefault(); drop.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (f) { const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files; showFile(f); }
  });
})();
