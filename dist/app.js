const SHIFT_CODES = ["M", "T", "N", "C", "L", "VAC", "INC", "LIC", "PER", "BLQ"];
const WORK = ["M", "T", "N", "C"];
const SHIFTS = {
  M: { start: 7, end: 13, hours: 6 },
  T: { start: 13, end: 19, hours: 6 },
  N: { start: 19, end: 31, hours: 12 },
  C: { start: 7, end: 19, hours: 12 },
  L: { hours: 0 },
  VAC: { hours: 0 },
  INC: { hours: 0 },
  LIC: { hours: 0 },
  PER: { hours: 0 },
  BLQ: { hours: 0 },
};

const STAFF = [
  ["D001", "Camila Torres", "DIRECTO"],
  ["D002", "Mateo Ramirez", "DIRECTO"],
  ["D003", "Isabella Moreno", "DIRECTO"],
  ["D004", "Daniel Rojas", "DIRECTO"],
  ["D005", "Valentina Castro", "DIRECTO"],
  ["D006", "Santiago Herrera", "DIRECTO"],
  ["D007", "Mariana Duarte", "DIRECTO"],
  ["D008", "Andres Salazar", "DIRECTO"],
  ["D009", "Natalia Pardo", "DIRECTO"],
  ["D010", "Felipe Cardenas", "DIRECTO"],
  ["D011", "Paula Mejia", "DIRECTO"],
  ["OPS1", "Julian Vargas", "OPS"],
  ["OPS2", "Lucia Bernal", "OPS"],
].map((row, index) => ({
  id: index + 1,
  code: row[0],
  name: row[1],
  type: row[2],
  salary: row[2] === "DIRECTO" ? 5970000 : 0,
  targetHours: row[2] === "DIRECTO" ? 210 : 0,
  maxHours: row[2] === "OPS" ? (index === 11 ? 120 : 96) : 240,
  desiredHours: row[2] === "OPS" ? (index === 11 ? 96 : 72) : 0,
  dayRate: row[2] === "OPS" ? 30000 : 0,
  nightRate: row[2] === "OPS" ? 40000 : 0,
}));

const LEGAL = {
  monthlyDivisor: 210,
  nightSurcharge: 0.35,
  overtimeDay: 0.25,
  overtimeNight: 0.75,
  mandatoryRest: 0.90,
  health: 0.04,
  pension: 0.04,
  uvt: 52374,
  exempt: 0.25,
  minimumWage: 1750905,
};

const EVENT_CODE = {
  VACACIONES: "VAC",
  INCAPACIDAD_COMUN: "INC",
  INCAPACIDAD_LABORAL: "INC",
  LICENCIA_REMUNERADA: "LIC",
  LICENCIA_NO_REMUNERADA: "LIC",
  PERMISO_REMUNERADO: "PER",
  PERMISO_NO_REMUNERADO: "PER",
};

const EVENT_LABEL = {
  VACACIONES: "Vacaciones",
  INCAPACIDAD_COMUN: "Incapacidad comun",
  INCAPACIDAD_LABORAL: "Incapacidad laboral",
  LICENCIA_REMUNERADA: "Licencia remunerada",
  LICENCIA_NO_REMUNERADA: "Licencia no remunerada",
  PERMISO_REMUNERADO: "Permiso remunerado",
  PERMISO_NO_REMUNERADO: "Permiso no remunerado",
};

let state = {
  year: 2026,
  month: 4,
  need: { M: 2, T: 2, N: 2, C: 0 },
  schedule: [],
  novelties: [],
};

function pad(value) {
  return String(value).padStart(2, "0");
}

function iso(year, month, day) {
  return `${year}-${pad(month)}-${pad(day)}`;
}

function parseIso(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function money(value) {
  return `$${Math.round(value || 0).toLocaleString("es-CO")}`;
}

function html(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function isOpsAvailable(staffId, day, shift) {
  const person = STAFF.find(s => s.id === staffId);
  if (!person || person.type !== "OPS") return true;
  return ((staffId + day) % 2 === 0) || shift === "N";
}

function isBlocked(staffId, day) {
  const date = iso(state.year, state.month, day);
  return state.novelties.some(n => n.staffId === staffId && n.blocks && n.start <= date && n.end >= date);
}

function protectedCode(staffId, day) {
  const date = iso(state.year, state.month, day);
  const match = state.novelties.find(n => n.staffId === staffId && n.blocks && n.start <= date && n.end >= date);
  return match ? EVENT_CODE[match.type] || "BLQ" : null;
}

function generateSchedule() {
  const totalDays = daysInMonth(state.year, state.month);
  const rows = [];
  const totals = Object.fromEntries(STAFF.map(s => [s.id, 0]));
  const nights = Object.fromEntries(STAFF.map(s => [s.id, 0]));
  for (const person of STAFF) {
    for (let day = 1; day <= totalDays; day++) {
      rows.push({ staffId: person.id, day, shift: "L", source: "AUTO" });
    }
  }
  for (let day = 1; day <= totalDays; day++) {
    for (const shift of WORK) {
      const required = state.need[shift] || 0;
      for (let slot = 0; slot < required; slot++) {
        const candidates = STAFF.filter(person => {
          const row = rows.find(r => r.staffId === person.id && r.day === day);
          if (!row || row.shift !== "L") return false;
          if (isBlocked(person.id, day)) return false;
          if (!isOpsAvailable(person.id, day, shift)) return false;
          const previous = rows.find(r => r.staffId === person.id && r.day === day - 1);
          if (previous && previous.shift === "N" && ["M", "T", "C"].includes(shift)) return false;
          return totals[person.id] + SHIFTS[shift].hours <= person.maxHours;
        }).sort((a, b) => {
          const biasA = a.type === "OPS" ? 10 : 0;
          const biasB = b.type === "OPS" ? 10 : 0;
          return (totals[a.id] + biasA + (shift === "N" ? nights[a.id] * 2 : 0)) -
                 (totals[b.id] + biasB + (shift === "N" ? nights[b.id] * 2 : 0));
        });
        if (candidates.length) {
          const chosen = candidates[0];
          const row = rows.find(r => r.staffId === chosen.id && r.day === day);
          row.shift = shift;
          totals[chosen.id] += SHIFTS[shift].hours;
          if (shift === "N") nights[chosen.id] += 1;
        }
      }
    }
  }
  state.schedule = rows;
  applyNoveltyCodes();
}

function applyNoveltyCodes() {
  for (const row of state.schedule) {
    const code = protectedCode(row.staffId, row.day);
    if (code) {
      row.shift = code;
      row.source = "NOVEDAD";
    }
  }
}

function setShift(staffId, day, shift) {
  const row = state.schedule.find(r => r.staffId === staffId && r.day === day);
  if (!row) return;
  row.shift = shift;
  row.source = "MANUAL";
  render();
}

function assignedCount(day, shift) {
  return state.schedule.filter(r => r.day === day && r.shift === shift).length;
}

function coverageRows() {
  const rows = [];
  for (let day = 1; day <= daysInMonth(state.year, state.month); day++) {
    for (const shift of WORK) {
      const required = state.need[shift] || 0;
      if (!required) continue;
      const assigned = assignedCount(day, shift);
      rows.push({ day, shift, required, assigned, diff: assigned - required });
    }
  }
  return rows;
}

function summaryByStaff() {
  return STAFF.map(person => {
    const rows = state.schedule.filter(r => r.staffId === person.id);
    const worked = rows.filter(r => WORK.includes(r.shift));
    const hours = worked.reduce((sum, r) => sum + SHIFTS[r.shift].hours, 0);
    const nights = worked.filter(r => r.shift === "N").length;
    const weekends = worked.filter(r => isWeekend(r.day)).length;
    const festivos = worked.filter(r => isHoliday(r.day)).length;
    const protectedHours = protectedDays(person.id) * 7;
    return { ...person, hours, nights, weekends, festivos, protectedHours, recognized: hours + protectedHours };
  });
}

function protectedDays(staffId) {
  const dates = new Set();
  for (const n of state.novelties) {
    if (n.staffId !== staffId || !n.blocks) continue;
    let current = parseIso(n.start);
    const end = parseIso(n.end);
    while (current <= end) {
      if (current.getFullYear() === state.year && current.getMonth() + 1 === state.month) {
        dates.add(current.getDate());
      }
      current.setDate(current.getDate() + 1);
    }
  }
  return dates.size;
}

function isWeekend(day) {
  const dow = new Date(state.year, state.month - 1, day).getDay();
  return dow === 0 || dow === 6;
}

function isHoliday(day) {
  const fixed = ["01-01", "05-01", "07-20", "08-07", "12-08", "12-25"];
  return fixed.includes(`${pad(state.month)}-${pad(day)}`) || new Date(state.year, state.month - 1, day).getDay() === 0;
}

function splitShift(day, shift) {
  if (!WORK.includes(shift)) return [];
  const data = SHIFTS[shift];
  let start = data.start;
  let end = data.end;
  const out = [];
  for (let hour = start; hour < end; hour++) {
    const absolute = hour;
    const clock = absolute % 24;
    const dateOffset = absolute >= 24 ? 1 : 0;
    const segmentDate = new Date(state.year, state.month - 1, day + dateOffset);
    const night = clock >= 19 || clock < 6;
    out.push({ hours: 1, night, date: segmentDate, rest: segmentDate.getDay() === 0 || isHoliday(segmentDate.getDate()) });
  }
  return out;
}

function dayNightHours(day, shift) {
  return splitShift(day, shift).reduce((acc, s) => {
    if (s.night) acc.night += s.hours;
    else acc.day += s.hours;
    return acc;
  }, { day: 0, night: 0 });
}

function renderCalendar(containerId, type) {
  const people = STAFF.filter(s => s.type === type);
  const totalDays = daysInMonth(state.year, state.month);
  let out = `<table class="cal"><thead><tr><th>Nombre</th>`;
  for (let day = 1; day <= totalDays; day++) out += `<th>${day}</th>`;
  out += `</tr></thead><tbody>`;
  for (const person of people) {
    out += `<tr><td class="name">${html(person.name)}</td>`;
    for (let day = 1; day <= totalDays; day++) {
      const row = state.schedule.find(r => r.staffId === person.id && r.day === day);
      const value = row ? row.shift : "L";
      out += `<td><select class="shift-${value}" data-staff="${person.id}" data-day="${day}">`;
      for (const code of SHIFT_CODES) out += `<option value="${code}" ${code === value ? "selected" : ""}>${code}</option>`;
      out += `</select></td>`;
    }
    out += `</tr>`;
  }
  out += `</tbody></table>`;
  document.getElementById(containerId).innerHTML = out;
  document.querySelectorAll(`#${containerId} select`).forEach(select => {
    select.addEventListener("change", event => {
      setShift(Number(event.target.dataset.staff), Number(event.target.dataset.day), event.target.value);
    });
  });
}

function renderCoverage() {
  const rows = coverageRows();
  const totalDays = daysInMonth(state.year, state.month);
  const problems = rows.filter(r => r.diff !== 0).length;
  let out = `<div class="${problems ? "badcell" : "ok"}">${problems ? `Hay ${problems} turno(s) con personal faltante o sobrante.` : "Cobertura exacta en todos los turnos."}</div>`;
  out += `<table class="coverage"><thead><tr><th>Turno</th>`;
  for (let day = 1; day <= totalDays; day++) out += `<th>${day}</th>`;
  out += `</tr></thead><tbody>`;
  for (const shift of WORK) {
    out += `<tr><td><strong>${shift}</strong></td>`;
    for (let day = 1; day <= totalDays; day++) {
      const required = state.need[shift] || 0;
      const assigned = assignedCount(day, shift);
      const diff = assigned - required;
      if (!required && !assigned) out += `<td class="okcell">-</td>`;
      else if (!diff) out += `<td class="okcell">${assigned}/${required}</td>`;
      else out += `<td class="badcell">${diff < 0 ? `Falta ${Math.abs(diff)}` : `Sobra ${diff}`}<br><small>${assigned}/${required}</small></td>`;
    }
    out += `</tr>`;
  }
  out += `</tbody></table>`;
  document.getElementById("coverage").innerHTML = out;
}

function renderKpis() {
  const coverage = coverageRows().filter(r => r.required > 0);
  const complete = coverage.filter(r => r.assigned === r.required).length;
  const coveragePct = coverage.length ? complete / coverage.length * 100 : 100;
  const summaries = summaryByStaff();
  const direct = summaries.filter(s => s.type === "DIRECTO");
  const range = key => Math.max(...direct.map(s => s[key])) - Math.min(...direct.map(s => s[key]));
  const opsTotal = opsDetails().reduce((sum, r) => sum + r.total, 0);
  const netTotal = directPayroll().reduce((sum, p) => sum + p.net, 0);
  const alerts = coverage.filter(r => r.diff !== 0).length;
  document.getElementById("kpis").innerHTML = `
    <div class="card"><div class="muted">Cobertura completa</div><div class="metric">${coveragePct.toFixed(0)}%</div><div class="muted">${complete}/${coverage.length} turnos</div></div>
    <div class="card"><div class="muted">Equidad carga reconocida</div><div class="metric">${range("recognized").toFixed(0)} h</div><div class="muted">Trabajadas ${range("hours").toFixed(0)} h · Noches ${range("nights")} · FDS ${range("weekends")} · Fest. ${range("festivos")}</div></div>
    <div class="card"><div class="muted">Alertas</div><div class="metric">${alerts}</div></div>
    <div class="card"><div class="muted">Total OPS</div><div class="metric">${money(opsTotal)}</div></div>
    <div class="card"><div class="muted">Neto nomina directa</div><div class="metric">${money(netTotal)}</div></div>
    <div class="card"><div class="muted">Modo</div><div class="metric">Editable</div><div class="muted">Cambios locales del navegador</div></div>
  `;
}

function renderNovelties() {
  if (!state.novelties.length) {
    document.getElementById("novelties").innerHTML = `<p class="muted">No hay novedades registradas.</p>`;
    return;
  }
  let out = `<table class="table"><thead><tr><th>Medico</th><th>Tipo</th><th>Desde</th><th>Hasta</th><th>Bloquea</th><th>Accion</th></tr></thead><tbody>`;
  for (const n of state.novelties) {
    const person = STAFF.find(s => s.id === n.staffId);
    out += `<tr><td>${html(person.name)}</td><td>${html(EVENT_LABEL[n.type])}</td><td>${n.start}</td><td>${n.end}</td><td>${n.blocks ? "Si" : "No"}</td><td><button class="danger" data-delete="${n.id}">Borrar</button></td></tr>`;
  }
  out += `</tbody></table>`;
  document.getElementById("novelties").innerHTML = out;
  document.querySelectorAll("[data-delete]").forEach(button => {
    button.addEventListener("click", () => {
      state.novelties = state.novelties.filter(n => n.id !== Number(button.dataset.delete));
      generateSchedule();
      render();
    });
  });
}

function addNovelty(form, incapacity = false) {
  const data = new FormData(form);
  const start = data.get("start");
  const end = data.get("end") || start;
  if (!start) return;
  const novelty = {
    id: Date.now(),
    staffId: Number(data.get("staffId")),
    type: data.get("type"),
    start: start <= end ? start : end,
    end: start <= end ? end : start,
    blocks: incapacity ? true : data.get("blocks") === "1",
    accumulated: Number(data.get("accumulated") || 1),
  };
  state.novelties.push(novelty);
  applyNoveltyCodes();
  render();
}

function coverageCandidates(day, shift) {
  return STAFF.filter(person => {
    const row = state.schedule.find(r => r.staffId === person.id && r.day === day);
    if (!row || WORK.includes(row.shift) || ["VAC", "INC", "LIC", "PER", "BLQ"].includes(row.shift)) return false;
    if (isBlocked(person.id, day)) return false;
    if (!isOpsAvailable(person.id, day, shift)) return false;
    const previous = state.schedule.find(r => r.staffId === person.id && r.day === day - 1);
    if (previous && previous.shift === "N" && ["M", "T", "C"].includes(shift)) return false;
    return true;
  });
}

function renderCoverageAssignments() {
  const gaps = coverageRows().filter(r => r.diff < 0);
  if (!gaps.length) {
    document.getElementById("coverageAssignments").innerHTML = `<p class="ok">No hay turnos pendientes por cubrir.</p>`;
    return;
  }
  let out = `<table class="table"><thead><tr><th>Fecha</th><th>Turno</th><th>Faltan</th><th>Candidato</th><th>Accion</th></tr></thead><tbody>`;
  for (const gap of gaps) {
    const candidates = coverageCandidates(gap.day, gap.shift);
    out += `<tr><td>${iso(state.year, state.month, gap.day)}</td><td>${gap.shift}</td><td>${Math.abs(gap.diff)}</td><td>`;
    if (candidates.length) {
      out += `<select data-cover-day="${gap.day}" data-cover-shift="${gap.shift}">`;
      for (const c of candidates) out += `<option value="${c.id}">${html(c.name)} (${c.type})</option>`;
      out += `</select></td><td><button data-assign-day="${gap.day}" data-assign-shift="${gap.shift}">Asignar cobertura</button></td>`;
    } else {
      out += `<span class="muted">Sin candidatos sin romper reglas</span></td><td>Revisar disponibilidad</td>`;
    }
    out += `</tr>`;
  }
  out += `</tbody></table>`;
  document.getElementById("coverageAssignments").innerHTML = out;
  document.querySelectorAll("[data-assign-day]").forEach(button => {
    button.addEventListener("click", () => {
      const day = Number(button.dataset.assignDay);
      const shift = button.dataset.assignShift;
      const select = document.querySelector(`select[data-cover-day="${day}"][data-cover-shift="${shift}"]`);
      const staffId = Number(select.value);
      const row = state.schedule.find(r => r.staffId === staffId && r.day === day);
      row.shift = shift;
      row.source = "COBERTURA";
      render();
    });
  });
}

function extraRows() {
  const out = [];
  for (const person of STAFF.filter(s => s.type === "DIRECTO")) {
    let running = 0;
    for (let day = 1; day <= daysInMonth(state.year, state.month); day++) {
      const row = state.schedule.find(r => r.staffId === person.id && r.day === day);
      if (!row || !WORK.includes(row.shift)) continue;
      const shiftHours = SHIFTS[row.shift].hours;
      const regularRemaining = Math.max(LEGAL.monthlyDivisor - running, 0);
      let extraDay = 0, extraNight = 0, extraRest = 0;
      let cursor = 0;
      for (const segment of splitShift(day, row.shift)) {
        const extra = Math.max(cursor + segment.hours - Math.max(regularRemaining, cursor), 0);
        if (extra) {
          if (segment.rest) extraRest += extra;
          else if (segment.night) extraNight += extra;
          else extraDay += extra;
        }
        cursor += segment.hours;
      }
      running += shiftHours;
      out.push({ staffId: person.id, name: person.name, day, total: extraDay + extraNight + extraRest, extraDay, extraNight, extraRest });
    }
  }
  return out;
}

function renderExtraHours() {
  const rows = extraRows();
  const totalDays = daysInMonth(state.year, state.month);
  let out = `<table class="extra-cal"><thead><tr><th>Medico</th>`;
  for (let day = 1; day <= totalDays; day++) out += `<th>${day}</th>`;
  out += `<th>Total</th><th>Valor estimado</th></tr></thead><tbody>`;
  const summary = [];
  for (const person of STAFF.filter(s => s.type === "DIRECTO")) {
    const personRows = rows.filter(r => r.staffId === person.id);
    const hourly = person.salary / LEGAL.monthlyDivisor;
    const totals = personRows.reduce((a, r) => {
      a.day += r.extraDay; a.night += r.extraNight; a.rest += r.extraRest; return a;
    }, { day: 0, night: 0, rest: 0 });
    const value = totals.day * hourly * (1 + LEGAL.overtimeDay) + totals.night * hourly * (1 + LEGAL.overtimeNight) + totals.rest * hourly * (1 + LEGAL.mandatoryRest);
    summary.push({ medico: person.name, extra_diurna: totals.day, extra_nocturna: totals.night, extra_dominical_festiva: totals.rest, valor_total_extra: Math.round(value) });
    out += `<tr><td class="name">${html(person.name)}</td>`;
    for (let day = 1; day <= totalDays; day++) {
      const total = personRows.filter(r => r.day === day).reduce((s, r) => s + r.total, 0);
      out += `<td class="${total ? "extra-hot" : "extra-zero"}">${total ? total.toFixed(0) : ""}</td>`;
    }
    out += `<td><strong>${(totals.day + totals.night + totals.rest).toFixed(0)}</strong></td><td><strong>${money(value)}</strong></td></tr>`;
  }
  out += `</tbody></table><h3>Resumen horas extra a pagar con recargos</h3>${table(summary)}`;
  document.getElementById("extraHours").innerHTML = out;
}

function withholding(base) {
  const uvt = base / LEGAL.uvt;
  let tax = 0;
  if (uvt > 2300) tax = (uvt - 2300) * 0.39 + 770;
  else if (uvt > 945) tax = (uvt - 945) * 0.37 + 268;
  else if (uvt > 640) tax = (uvt - 640) * 0.35 + 162;
  else if (uvt > 360) tax = (uvt - 360) * 0.33 + 69;
  else if (uvt > 150) tax = (uvt - 150) * 0.28 + 10;
  else if (uvt > 95) tax = (uvt - 95) * 0.19;
  return tax * LEGAL.uvt;
}

function noveltyDeduction(person) {
  let amount = 0;
  const daily = person.salary / 30;
  for (const n of state.novelties.filter(x => x.staffId === person.id)) {
    const days = overlapDays(n.start, n.end);
    if (n.type === "LICENCIA_NO_REMUNERADA" || n.type === "PERMISO_NO_REMUNERADO") amount += days * daily;
    if (n.type === "INCAPACIDAD_COMUN" && person.salary > LEGAL.minimumWage) {
      for (let i = 0; i < days; i++) {
        const d = (n.accumulated || 1) + i;
        const rate = d <= 2 ? 1 : d <= 90 ? 2 / 3 : 0.5;
        amount += daily * (1 - rate);
      }
    }
  }
  return amount;
}

function overlapDays(start, end) {
  let current = parseIso(start);
  const last = parseIso(end);
  let count = 0;
  while (current <= last) {
    if (current.getFullYear() === state.year && current.getMonth() + 1 === state.month) count++;
    current.setDate(current.getDate() + 1);
  }
  return count;
}

function directPayroll() {
  const extras = extraRows();
  return STAFF.filter(s => s.type === "DIRECTO").map(person => {
    const rows = state.schedule.filter(r => r.staffId === person.id && WORK.includes(r.shift));
    const hourly = person.salary / LEGAL.monthlyDivisor;
    let nightHours = 0;
    let restHours = 0;
    for (const row of rows) {
      for (const segment of splitShift(row.day, row.shift)) {
        if (segment.night) nightHours += segment.hours;
        if (segment.rest) restHours += segment.hours;
      }
    }
    const personExtra = extras.filter(r => r.staffId === person.id);
    const extraDay = personExtra.reduce((s, r) => s + r.extraDay, 0);
    const extraNight = personExtra.reduce((s, r) => s + r.extraNight, 0);
    const extraRest = personExtra.reduce((s, r) => s + r.extraRest, 0);
    const recargoNocturno = nightHours * hourly * LEGAL.nightSurcharge;
    const extraDayValue = extraDay * hourly * (1 + LEGAL.overtimeDay);
    const extraNightValue = extraNight * hourly * (1 + LEGAL.overtimeNight);
    const restValue = (restHours + extraRest) * hourly * LEGAL.mandatoryRest;
    const gross = person.salary + recargoNocturno + extraDayValue + extraNightValue + restValue;
    const health = gross * LEGAL.health;
    const pension = gross * LEGAL.pension;
    const retentionBase = Math.max((gross - health - pension) * (1 - LEGAL.exempt), 0);
    const retention = withholding(retentionBase);
    const novelty = noveltyDeduction(person);
    const deductions = health + pension + retention + novelty;
    return { person, gross, health, pension, retention, novelty, deductions, net: gross - deductions, nightHours, extraDay, extraNight, restHours, hourly };
  });
}

function renderPayslips() {
  let out = "";
  for (const p of directPayroll()) {
    out += `<div class="payslip">
      <div class="payslip-head">
        <div><strong>${html(p.person.name)}</strong><br><span class="muted">Cargo: Medico general</span></div>
        <div><span class="muted">Basico</span><br><strong>${money(p.person.salary)}</strong></div>
        <div><span class="muted">Neto a pagar</span><br><strong class="net">${money(p.net)}</strong></div>
      </div>
      <div class="payslip-grid">
        <table class="table"><thead><tr><th>Devengos</th><th>Cantidad</th><th>Valor</th></tr></thead><tbody>
          <tr><td>Salario ordinario</td><td>30.00</td><td class="right">${money(p.person.salary)}</td></tr>
          <tr><td>Recargo nocturno</td><td>${p.nightHours.toFixed(2)}</td><td class="right">${money(p.nightHours * p.hourly * LEGAL.nightSurcharge)}</td></tr>
          <tr><td>Horas extras diurnas</td><td>${p.extraDay.toFixed(2)}</td><td class="right">${money(p.extraDay * p.hourly * (1 + LEGAL.overtimeDay))}</td></tr>
          <tr><td>Horas extras nocturnas</td><td>${p.extraNight.toFixed(2)}</td><td class="right">${money(p.extraNight * p.hourly * (1 + LEGAL.overtimeNight))}</td></tr>
        </tbody><tfoot><tr><th colspan="2">Subtotal</th><th class="right">${money(p.gross)}</th></tr></tfoot></table>
        <table class="table"><thead><tr><th>Deducciones</th><th>Cantidad</th><th>Valor</th></tr></thead><tbody>
          <tr><td>Salud empleado</td><td>4%</td><td class="right">${money(p.health)}</td></tr>
          <tr><td>Pension empleado</td><td>4%</td><td class="right">${money(p.pension)}</td></tr>
          <tr><td>Retencion en la fuente</td><td>-</td><td class="right">${money(p.retention)}</td></tr>
          <tr><td>Novedades no remuneradas/incapacidad</td><td>-</td><td class="right">${money(p.novelty)}</td></tr>
        </tbody><tfoot><tr><th colspan="2">Subtotal</th><th class="right">${money(p.deductions)}</th></tr></tfoot></table>
      </div>
      <div class="payslip-foot">Valor hora base: ${money(p.hourly)}. Estimacion para revision administrativa.</div>
    </div>`;
  }
  document.getElementById("payslips").innerHTML = out;
}

function opsDetails() {
  const rows = [];
  for (const person of STAFF.filter(s => s.type === "OPS")) {
    for (const row of state.schedule.filter(r => r.staffId === person.id && WORK.includes(r.shift))) {
      const split = dayNightHours(row.day, row.shift);
      rows.push({ person, day: row.day, shift: row.shift, dayHours: split.day, nightHours: split.night, total: split.day * person.dayRate + split.night * person.nightRate });
    }
  }
  return rows;
}

function renderOps() {
  const rows = opsDetails();
  const summary = STAFF.filter(s => s.type === "OPS").map(person => {
    const personRows = rows.filter(r => r.person.id === person.id);
    const dayHours = personRows.reduce((s, r) => s + r.dayHours, 0);
    const nightHours = personRows.reduce((s, r) => s + r.nightHours, 0);
    return {
      nombre: person.name,
      horas_deseadas: person.desiredHours,
      horas_asignadas: dayHours + nightHours,
      horas_diurnas: dayHours,
      horas_nocturnas: nightHours,
      total_a_pagar_ops: personRows.reduce((s, r) => s + r.total, 0),
    };
  });
  document.getElementById("opsSummary").innerHTML = table(summary);
}

function table(rows) {
  if (!rows.length) return `<p class="muted">Sin datos.</p>`;
  const keys = Object.keys(rows[0]);
  let out = `<table class="table"><thead><tr>${keys.map(k => `<th>${html(k)}</th>`).join("")}</tr></thead><tbody>`;
  for (const row of rows) {
    out += `<tr>${keys.map(k => `<td>${typeof row[k] === "number" ? (k.includes("valor") || k.includes("pagar") ? money(row[k]) : Number(row[k]).toFixed(2)) : html(row[k])}</td>`).join("")}</tr>`;
  }
  return out + `</tbody></table>`;
}

function render() {
  renderKpis();
  renderCalendar("directCalendar", "DIRECTO");
  renderCalendar("opsCalendar", "OPS");
  renderCoverage();
  renderNovelties();
  renderCoverageAssignments();
  renderExtraHours();
  renderPayslips();
  renderOps();
  localStorage.setItem("cuadroMedicoDemo", JSON.stringify(state));
}

function fillForms() {
  const params = new URLSearchParams(location.search);
  state.year = Number(params.get("year") || state.year);
  state.month = Number(params.get("month") || state.month);
  state.need.M = Number(params.get("m") || state.need.M);
  state.need.T = Number(params.get("t") || state.need.T);
  state.need.N = Number(params.get("n") || state.need.N);
  const monthSelect = document.getElementById("month");
  const months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
  monthSelect.innerHTML = months.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("");
  document.getElementById("year").value = state.year;
  monthSelect.value = state.month;
  document.getElementById("needM").value = state.need.M;
  document.getElementById("needT").value = state.need.T;
  document.getElementById("needN").value = state.need.N;
  const options = STAFF.map(s => `<option value="${s.id}">${html(s.name)} (${s.type})</option>`).join("");
  document.querySelectorAll('select[name="staffId"]').forEach(select => select.innerHTML = options);
  updateDateInputs();
}

function updateDateInputs() {
  const first = iso(state.year, state.month, 1);
  const last = iso(state.year, state.month, daysInMonth(state.year, state.month));
  document.querySelectorAll('input[type="date"]').forEach(input => {
    input.min = first;
    input.max = last;
    if (!input.value || input.value < first || input.value > last) input.value = first;
  });
}

function bind() {
  document.getElementById("regenerate").addEventListener("click", () => {
    state.year = Number(document.getElementById("year").value);
    state.month = Number(document.getElementById("month").value);
    state.need.M = Number(document.getElementById("needM").value);
    state.need.T = Number(document.getElementById("needT").value);
    state.need.N = Number(document.getElementById("needN").value);
    updateDateInputs();
    generateSchedule();
    render();
  });
  document.getElementById("resetDemo").addEventListener("click", () => {
    localStorage.removeItem("cuadroMedicoDemo");
    state.novelties = [];
    generateSchedule();
    render();
  });
  document.getElementById("generalNovelty").addEventListener("submit", event => {
    event.preventDefault();
    addNovelty(event.currentTarget, false);
  });
  document.getElementById("incapacityNovelty").addEventListener("submit", event => {
    event.preventDefault();
    addNovelty(event.currentTarget, true);
  });
}

function init() {
  const saved = localStorage.getItem("cuadroMedicoDemo");
  fillForms();
  if (saved) {
    try {
      state = JSON.parse(saved);
      document.getElementById("year").value = state.year;
      document.getElementById("month").value = state.month;
      document.getElementById("needM").value = state.need.M;
      document.getElementById("needT").value = state.need.T;
      document.getElementById("needN").value = state.need.N;
      updateDateInputs();
    } catch {
      generateSchedule();
    }
  } else {
    state.novelties = [{
      id: 1,
      staffId: STAFF.find(s => s.name === "Andres Salazar").id,
      type: "VACACIONES",
      start: iso(state.year, state.month, 3),
      end: iso(state.year, state.month, 5),
      blocks: true,
      accumulated: 1,
    }];
    generateSchedule();
  }
  bind();
  render();
}

init();
