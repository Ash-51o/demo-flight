const form = document.getElementById('queryForm');
const input = document.getElementById('nNumber');
const spinner = document.getElementById('spinner');
const errBox = document.getElementById('error');
const results = document.getElementById('results');

const fr24Card = document.getElementById('fr24Card');
const fr24Link = document.getElementById('fr24Link');
const regCard = document.getElementById('regCard');
const regLink = document.getElementById('regLink');
const adsbCard = document.getElementById('adsbCard');
const adsbLink = document.getElementById('adsbLink');

const targetCard = document.getElementById('targetCard');
const activityCard = document.getElementById('activityCard');
const flightsTable = document.getElementById('flightsTable');

function showSpinner(show) { spinner.style.display = show ? 'inline-block' : 'none'; }
function setError(msg) {
  if (!msg) { errBox.classList.add('d-none'); errBox.textContent = ''; return; }
  errBox.classList.remove('d-none'); errBox.textContent = msg;
}
function kv(label, value){
  return `<div class="mb-2"><div class="label">${label}</div><div class="value">${value || '—'}</div></div>`;
}
function pill(code, count){
  return `<span class="badge rounded-pill text-bg-primary me-2 badge-airport">${code || '—'} <span class="ms-1 text-bg-dark badge">${count}</span></span>`;
}
function formatAgo(epoch){
  if (!epoch) return '—';
  const diffMs = Date.now() - (epoch * 1000);
  const s = Math.max(0, Math.floor(diffMs / 1000));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d >= 1) return `${d} day${d>1?'s':''} ago`;
  if (h >= 1) return `${h} hr${h>1?'s':''} ago`;
  if (m >= 1) return `${m} min${m>1?'s':''} ago`;
  return `${s} sec${s>1?'s':''} ago`;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  setError('');
  results.classList.add('d-none');
  [fr24Card, regCard, adsbCard, targetCard, activityCard].forEach(el => el.innerHTML = '');
  [fr24Link, regLink, adsbLink].forEach(el => el.textContent = '');
  flightsTable.innerHTML = '';
  showSpinner(true);

  const n = (input.value || '').trim();
  if (!n) { setError('Please enter an N-number.'); showSpinner(false); return; }

  try {
    const res = await fetch(`/api/aircraft?n=${encodeURIComponent(n)}&use_adsb=true`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // ---------- FR24 Card ----------
    const fr = data.fr24 || {};
    fr24Card.innerHTML = [
      kv('Tail number', `<strong>${data.tail_number}</strong>`),
      kv('Aircraft', fr.model),
      kv('Airline', fr.airline),
      kv('Operator', fr.operator),
      kv('Type Code', fr.type_code),
      kv('Code (Airline)', fr.airline_code),
      kv('Code (Operator)', fr.operator_code),
      kv('Mode S (ICAO hex)', fr.mode_s || '—'),
      kv('Serial Number (MSN)', fr.serial_msn || '—'),
    ].join('');
    fr24Link.innerHTML = fr.source_url ? `Source: <a href="${fr.source_url}" target="_blank" rel="noopener">FR24</a>` : '';

    // ---------- Registry Card ----------
    const rg = data.registry || {};
    regCard.innerHTML = [
      kv('Owner', rg.owner),
      kv('Status', rg.status),
      kv('Airworthiness Class', rg.airworthiness_class),
      kv('Certificate Issue Date', rg.certificate_issue_date),
      kv('Airworthiness Date', rg.airworthiness_date),
      kv('Expiration', rg.expiration),
      kv('Engine', rg.engine),
      kv('Serial Number', rg.serial_number),
      kv('Model Year', rg.model_year),
      kv('Seats', rg.seats),
      kv('Engines', rg.engines_count),
      kv('Fractional Owner', (rg.fractional_owner === true ? 'YES' : (rg.fractional_owner === false ? 'NO' : '—'))),
    ].join('');
    regLink.innerHTML = rg.source_url ? `Source: <a href="${rg.source_url}" target="_blank" rel="noopener">FAA / Registry</a>` : '';

    // ---------- ADS-B Card ----------
    const ad = data.adsb || {};
    adsbCard.innerHTML = [
      kv('Callsign', ad.callsign),
      kv('ICAO hex', ad.hex),
      kv('Registration', ad.registration || data.tail_number),
      kv('Type (ICAO / Full)', [ad.icao_type, ad.type_full].filter(Boolean).join(' · ') || '—'),
      kv('Type Desc / Category', [ad.type_desc, ad.category].filter(Boolean).join(' · ') || '—'),
      kv('Last seen (ADS-B)', ad.pos_epoch ? `${formatAgo(ad.pos_epoch)} (epoch)` : (ad.last_seen || '—')),
      kv('On-ground / Altitude', ad.baro_altitude || '—'),
      kv('Groundspeed (kt) / Track', [ad.groundspeed_kt, ad.ground_track].filter(Boolean).join(' / ') || '—'),
      kv('Heading (true/mag)', [ad.true_heading, ad.mag_heading].filter(Boolean).join(' / ') || '—'),
      kv('Squawk', ad.squawk),
      kv('Position (WGS84)', ad.position),
      kv('Source / Msg rate', [ad.source, ad.message_rate].filter(Boolean).join(' · ') || '—'),
    ].join('');
    adsbLink.innerHTML = data.links?.adsb_globe_url ? `Source: <a href="${data.links.adsb_globe_url}" target="_blank" rel="noopener">ADS-B Exchange (globe)</a>` : '';

    // ---------- Targeting ----------
    const roles = (data.buyer_roles_hint || []).map(r => `<span class="badge text-bg-secondary me-2">${r}</span>`).join('');
    const base = data.likely_base || {};
    const baseConf = (typeof base.confidence === 'number') ? ` (conf ${Math.round(base.confidence*100)}%)` : '';
    const ovn = (data.overnights_top || []).map(o =>
      `<span class="badge text-bg-primary me-2 badge-airport">${o.airport || '—'} <span class="ms-1 text-bg-dark badge">${o.overnights} ovn · ${o.avg_ground_hours}h avg</span></span>`
    ).join('');
    const chase = data.chase || {score:0, reasons:[]};
    targetCard.innerHTML = [
      kv('Inferred Operation', `${data.inferred_operation || '—'}${data.is_fractional ? ' (fractional)' : ''}`),
      kv('Who to contact', roles || '—'),
      kv('Likely operating base', base.code ? `${base.code}${baseConf}` : '—'),
      kv('Overnights / avg ground time', ovn || '—'),
      kv('Chase score', `${chase.score} / 5 — ${ (chase.reasons || []).join(', ') }`),
    ].join('');

    // ---------- Activity ----------
    const last = data.last_spotted || {};
    const placeLabel = (last.place_code ? (last.place_city ? `${last.place_code} (${last.place_city})` : last.place_code) : '—');
    const top7 = (data.top_airports_7d || []).map(a => pill(a.code, a.count)).join('');
    const top30 = (data.top_airports_30d || []).map(a => pill(a.code, a.count)).join('');
    const top90 = (data.top_airports_90d || []).map(a => pill(a.code, a.count)).join('');
    activityCard.innerHTML = [
      kv('Last spotted (overall)', last.epoch ? `${formatAgo(last.epoch)} at ${placeLabel}` : '—'),
      kv('Top airports — last 7 days', top7 || '—'),
      kv('Top airports — last 30 days', top30 || '—'),
      kv('Top airports — last 90 days', top90 || '—'),
    ].join('');

    // ---------- Flights Table ----------
    (data.recent_flights || []).forEach(f => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${f.date_local || '—'}</td>
        <td><span class="badge badge-airport text-bg-dark">${f.from_airport || '—'}</span></td>
        <td><span class="badge badge-airport text-bg-dark">${f.to_airport || '—'}</span></td>
        <td>${f.callsign || '—'}</td>
        <td>${f.flight_time || '—'}</td>
      `;
      flightsTable.appendChild(tr);
    });

    results.classList.remove('d-none');
  } catch (err) {
    console.error(err);
    setError('Lookup failed. Try again in a moment or verify the N-number.');
  } finally {
    showSpinner(false);
  }
});
