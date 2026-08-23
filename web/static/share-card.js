(function (global) {
  'use strict';

  var LOGICAL_WIDTH = 375;
  var SCALE = 3;
  var MAX_LOGICAL_HEIGHT = 2048;
  var RUNNING_TYPES = ['running', 'run', '跑步', 'trail_running', 'treadmill_running'];
  var FORBIDDEN_COACH_TEXT = /(睡眠|恢复|hrv|acwr|风险|警告|异常|建议)/i;
  var PALETTES = {
    dark: {bg: '#141a14', card: '#1e261e', subtle: '#1a201a', text: '#e6e8e3', secondary: '#9aa89a', muted: '#5a6b5a', border: '#2a342a', accent: '#f0a030', performance: '#5cb878', route: '#5cb878'},
    sport: {bg: '#f0f0f0', card: '#ffffff', subtle: '#f5f5f5', text: '#171717', secondary: '#525252', muted: '#a3a3a3', border: '#ebebeb', accent: '#f15b2a', performance: '#16a34a', route: '#16a34a'},
    fresh: {bg: '#e8efe9', card: '#ffffff', subtle: '#f0f4f0', text: '#1a2e23', secondary: '#5a7d6e', muted: '#8aa89a', border: '#dde5df', accent: '#2d9d6f', performance: '#22a86e', route: '#22a86e'}
  };
  var FONT_BODY = '-apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif';

  function fail(code, message) {
    var error = new Error(message);
    error.code = code;
    return error;
  }

  function number(value) {
    var parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function isRunningActivity(activity) {
    var values = [activityType(activity), activity.activity_name, activity.name, activity.display_name];
    var normalized = values.map(function (value) { return String(value || '').trim().toLowerCase(); }).join(' ');
    var primary = String(activity.primary_type || '').toLowerCase();
    if (/(cycling|bike|骑行|骑车|swim|游泳)/.test(normalized)) return false;
    if (['easy', 'long', 'tempo', 'interval', 'threshold', 'fartlek', 'recovery', 'race', 'aerobic'].indexOf(primary) !== -1) return true;
    return RUNNING_TYPES.some(function (type) { return normalized.indexOf(type) !== -1; });
  }

  function paceText(seconds) {
    var total = Math.round(number(seconds));
    if (!total) return '—';
    return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
  }

  function durationText(seconds) {
    var minutes = Math.round(number(seconds) / 60);
    if (!minutes) return '—';
    if (minutes < 60) return minutes + ' 分钟';
    return Math.floor(minutes / 60) + ' 小时 ' + (minutes % 60) + ' 分';
  }

  function activityType(activity) {
    return activity.activity_type || activity.type || activity.sport_type || activity.activityType;
  }

  function safeCoachText(value) {
    var text = typeof value === 'string' ? value.trim() : '';
    return text && !FORBIDDEN_COACH_TEXT.test(text) ? text : '';
  }

  function dailyCoachNotes(insight) {
    var sources = [
      {prefix: '运动概要：', label: '运动概要'},
      {prefix: '强度分布：', label: '强度分布'},
      {prefix: '跑步动力学：', label: '跑步动力学'},
      {prefix: '跑步分析：', label: '跑步分析'}
    ];
    var observations = Array.isArray(insight.observations) ? insight.observations : [];
    var notes = [];
    sources.forEach(function (source) {
      var match = observations.find(function (observation) {
        return typeof observation === 'string' && observation.trim().indexOf(source.prefix) === 0;
      });
      var text = match ? safeCoachText(match.trim().slice(source.prefix.length)) : '';
      if (text) notes.push({label: source.label, text: text});
    });
    var conclusion = safeCoachText(insight.conclusion);
    if (conclusion) notes.push({label: '教练结论', text: conclusion, conclusion: true});
    return notes;
  }

  function weeklyCoachNotes(report) {
    var sections = report.review_sections || {};
    var notes = [];
    var overview = safeCoachText((sections.overview || {}).headline);
    var trend = safeCoachText((sections.trend || {}).headline);
    var qualitySection = sections.quality_sessions || {};
    var qualityHeadline = safeCoachText(qualitySection.headline || qualitySection.summary);
    var qualityItems = Array.isArray(qualitySection.items) ? qualitySection.items : (Array.isArray(report.quality_sessions) ? report.quality_sessions : []);
    if (overview) notes.push({label: '本周概览', text: overview});
    if (qualityHeadline) notes.push({label: '质量课总结', text: qualityHeadline});
    else if (qualityItems.length) notes.push({label: '质量课总结', text: '本周识别 ' + qualityItems.length + ' 节质量课。'});
    if (trend) notes.push({label: '近期变化', text: trend});
    var conclusion = safeCoachText((report.finding || {}).conclusion);
    if (conclusion) notes.push({label: '教练结论', text: conclusion, conclusion: true});
    return notes;
  }

  function dailySessions(report) {
    var analyses = Array.isArray(report.session_analyses) ? report.session_analyses : [];
    var sessions = analyses.filter(function (analysis) {
      if (!analysis || typeof analysis !== 'object') return false;
      var summary = analysis.session_summary || {};
      var volume = summary.volume || {};
      return isRunningActivity(analysis) && number(volume.distance_m) > 0;
    }).map(function (analysis) {
      var summary = analysis.session_summary || {};
      var volume = summary.volume || {};
      var pace = summary.pace_profile || {};
      var structure = summary.structure || {};
      return {
        name: analysis.activity_name || analysis.name || summary.display_name || '跑步',
        distanceKm: number(volume.distance_m) / 1000,
        durationSeconds: number(volume.duration_s),
        paceSeconds: number(pace.avg_pace_sec_per_km),
        cadence: number(structure.avg_cadence)
      };
    });
    if (sessions.length) return sessions;

    var activities = report.daily_activities || report.yesterday_activities || {};
    return (Array.isArray(activities.sessions) ? activities.sessions : []).filter(function (activity) {
      return activity && isRunningActivity(activity);
    }).map(function (activity) {
      var distanceKm = number(activity.distance_km) || number(activity.distance_meters || activity.distance_m) / 1000;
      var durationSeconds = number(activity.duration_seconds || activity.duration_s) || number(activity.duration_min) * 60;
      return {
        name: activity.name || activity.activity_name || '跑步',
        distanceKm: distanceKm,
        durationSeconds: durationSeconds,
        paceSeconds: number(activity.pace_sec_per_km) || (distanceKm ? durationSeconds / distanceKm : 0),
        cadence: number(activity.cadence || activity.avg_cadence)
      };
    }).filter(function (activity) { return activity.distanceKm > 0; });
  }

  function buildDailyShareCardData(report, theme) {
    if (!report || report.has_data === false) throw fail('no_running_activity', '这一天没有可分享的跑步数据。');
    var sessions = dailySessions(report);
    if (!sessions.length) throw fail('no_running_activity', '休息日没有跑步数据，暂不生成分享卡。');
    var distance = sessions.reduce(function (sum, session) { return sum + session.distanceKm; }, 0);
    var duration = sessions.reduce(function (sum, session) { return sum + session.durationSeconds; }, 0);
    var cadence = sessions.map(function (session) { return session.cadence; }).filter(Boolean)[0] || 0;
    var weightedPace = sessions.reduce(function (sum, session) { return sum + session.paceSeconds * session.distanceKm; }, 0);
    var insight = report.ai_insight || {};
    var coachNotes = dailyCoachNotes(insight);
    return {
      kind: 'daily',
      theme: PALETTES[theme] ? theme : 'sport',
      eyebrow: '跑步',
      date: String(report.report_date || '').slice(0, 10),
      title: sessions.length > 1 ? sessions.length + ' 次跑步' : '跑步',
      distanceKm: distance,
      stats: [
        {label: '时长', value: durationText(duration)},
        {label: '配速', value: paceText(weightedPace / distance) + ' /km'},
        {label: '步频', value: cadence ? Math.round(cadence) + ' spm' : '—'}
      ],
      coachNotes: coachNotes,
      details: sessions.slice().reverse().map(function (session) {
        return {label: session.name || '跑步', distance: session.distanceKm, duration: durationText(session.durationSeconds), pace: paceText(session.paceSeconds)};
      })
    };
  }

  function buildWeeklyShareCardData(report, theme) {
    if (!report || typeof report !== 'object') throw fail('no_running_activity', '未找到已归档的周复盘。');
    var actual = report.actual_summary || {};
    var distance = number(actual.running_distance_km || actual.running_km);
    var days = number(actual.running_days);
    if (!distance && !days) throw fail('no_running_activity', '这一周没有跑步数据，暂不生成分享卡。');
    var longest = actual.longest_running_activity || {};
    var qualities = Array.isArray(report.quality_sessions) ? report.quality_sessions : [];
    var coachNotes = weeklyCoachNotes(report);
    return {
      kind: 'weekly',
      theme: PALETTES[theme] ? theme : 'sport',
      eyebrow: '跑步',
      date: '本周训练' + ([report.week_start, report.week_end].filter(Boolean).length ? ' · ' + [report.week_start, report.week_end].filter(Boolean).join('–') : ''),
      title: '跑步',
      distanceKm: distance,
      stats: [
        {label: '最长单次', value: number(longest.distance_km).toFixed(1) + ' km'},
        {label: '平均配速', value: paceText(actual.running_pace_sec_per_km) + ' /km'},
        {label: '跑步天数', value: Math.round(days) + ' 天'}
      ],
      coachNotes: coachNotes,
      details: qualities.slice(0, 3).map(function (session) {
        return {label: session.label || session.quality_type || '质量训练', distance: number(session.distance_km), duration: '', pace: paceText((session.metrics || {}).average_pace)};
      })
    };
  }

  function roundedRect(ctx, x, y, width, height, radius) {
    ctx.beginPath();
    ctx.roundRect(x, y, width, height, radius);
  }

  function drawRoute(ctx, palette, y) {
    ctx.save();
    ctx.strokeStyle = palette.route;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([2, 6]);
    ctx.beginPath();
    ctx.moveTo(40, y + 18);
    ctx.bezierCurveTo(95, y - 15, 140, y + 36, 198, y + 8);
    ctx.bezierCurveTo(252, y - 18, 299, y + 20, 338, y - 4);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = palette.performance;
    ctx.beginPath(); ctx.arc(40, y + 18, 3.5, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = palette.accent;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(338, y - 4, 5, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }

  function drawRule(ctx, palette, y) {
    ctx.strokeStyle = palette.border;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(20, y + .5); ctx.lineTo(355, y + .5); ctx.stroke();
  }

  function wrapText(ctx, text, maxWidth, maxLines) {
    var lines = [];
    var line = '';
    Array.from(String(text || '').trim()).some(function (character) {
      var candidate = line + character;
      if (line && ctx.measureText(candidate).width > maxWidth) {
        lines.push(line);
        line = character;
        return lines.length >= maxLines;
      }
      line = candidate;
      return false;
    });
    if (line && lines.length < maxLines) lines.push(line);
    if (lines.length === maxLines && ctx.measureText(String(text || '')).width > maxWidth * maxLines) {
      lines[maxLines - 1] = lines[maxLines - 1].slice(0, -1) + '…';
    }
    return lines;
  }

  function measureCoachNotes(viewModel, ctx) {
    var notes = (viewModel.coachNotes || []).map(function (note) {
      var conclusion = Boolean(note.conclusion);
      ctx.font = conclusion
        ? '700 15px ' + FONT_BODY
        : '500 12px ' + FONT_BODY;
      return {
        label: note.label,
        conclusion: conclusion,
        lines: wrapText(ctx, note.text, 290, conclusion ? 3 : 2),
      };
    }).filter(function (note) { return note.lines.length; });
    var height = notes.reduce(function (total, note) {
      return total + (note.conclusion ? 27 + note.lines.length * 22 + 10 : 14 + note.lines.length * 17 + 7);
    }, 16);
    return {notes: notes, height: notes.length ? height : 0};
  }

  function measureShareCard(viewModel, ctx) {
    var coach = measureCoachNotes(viewModel, ctx);
    var detailCount = Math.min(viewModel.details.length, 4);
    var height = 330 + coach.height + (detailCount ? 50 + detailCount * 34 : 0) + 54;
    if (height > MAX_LOGICAL_HEIGHT) throw fail('content_too_tall', '分享卡内容过长，请缩短报告结论后重试。');
    return {height: Math.max(430, height), coach: coach, detailCount: detailCount};
  }

  function drawShareCard(viewModel, canvas, layout) {
    var palette = PALETTES[viewModel.theme];
    var ctx = canvas.getContext('2d');
    ctx.scale(SCALE, SCALE);
    ctx.fillStyle = palette.bg;
    ctx.fillRect(0, 0, LOGICAL_WIDTH, layout.height);
    ctx.fillStyle = palette.accent;
    roundedRect(ctx, 20, 20, 25, 25, 7);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.font = '900 13px system-ui, sans-serif';
    ctx.fillText('N', 26, 37);
    ctx.fillStyle = palette.text;
    ctx.font = '800 14px system-ui, sans-serif';
    ctx.fillText('neurun', 52, 35);
    ctx.fillStyle = palette.muted;
    ctx.font = '600 9px system-ui, sans-serif';
    ctx.fillText('RUNNING NOTES', 52, 48);

    ctx.fillStyle = palette.muted;
    ctx.font = '600 11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(viewModel.kind === 'weekly' ? 'WEEKLY REVIEW' : 'DAILY RUN', 355, 34);
    ctx.font = '500 10px system-ui, sans-serif';
    ctx.fillText(viewModel.date, 355, 49);
    ctx.textAlign = 'start';
    drawRule(ctx, palette, 66);

    drawRoute(ctx, palette, 91);
    ctx.textAlign = 'center';
    ctx.fillStyle = palette.accent;
    ctx.font = '800 11px system-ui, sans-serif';
    ctx.fillText(viewModel.eyebrow.toUpperCase(), LOGICAL_WIDTH / 2, 98);
    ctx.fillStyle = palette.text;
    ctx.font = '800 64px "Arial Narrow", "Helvetica Neue", system-ui, sans-serif';
    ctx.fillText(viewModel.distanceKm.toFixed(2), LOGICAL_WIDTH / 2 - 9, 164);
    var distanceWidth = ctx.measureText(viewModel.distanceKm.toFixed(2)).width;
    ctx.fillStyle = palette.muted;
    ctx.font = '700 14px system-ui, sans-serif';
    ctx.fillText('KM', LOGICAL_WIDTH / 2 + distanceWidth / 2 + 6, 163);
    ctx.textAlign = 'start';
    drawRule(ctx, palette, 188);
    var statWidth = 111;
    viewModel.stats.forEach(function (stat, index) {
      var x = 20 + index * 112;
      var centerX = x + statWidth / 2;
      if (index) {
        ctx.strokeStyle = palette.border;
        ctx.beginPath(); ctx.moveTo(x, 203); ctx.lineTo(x, 250); ctx.stroke();
      }
      ctx.fillStyle = palette.text;
      ctx.font = '800 15px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(stat.value, centerX, 218);
      ctx.fillStyle = palette.muted;
      ctx.font = '700 9px system-ui, sans-serif';
      ctx.fillText(stat.label.toUpperCase(), centerX, 238);
      ctx.textAlign = 'start';
    });

    var detailsY = 282;
    if (layout.coach.notes.length) {
      var coachY = 274;
      ctx.fillStyle = palette.subtle;
      roundedRect(ctx, 20, coachY, 335, layout.coach.height, 10);
      ctx.fill();
      ctx.fillStyle = palette.accent;
      ctx.fillRect(20, coachY, 4, layout.coach.height);
      var lineY = coachY + 23;
      layout.coach.notes.forEach(function (note) {
        if (note.conclusion) {
          ctx.strokeStyle = palette.border;
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(38, lineY - 12); ctx.lineTo(337, lineY - 12); ctx.stroke();
          lineY += 2;
        }
        ctx.fillStyle = note.conclusion ? palette.accent : palette.muted;
        ctx.font = note.conclusion ? '800 10px ' + FONT_BODY : '700 10px ' + FONT_BODY;
        ctx.fillText(note.label.toUpperCase(), 38, lineY);
        ctx.fillStyle = note.conclusion ? palette.text : palette.secondary;
        ctx.font = note.conclusion
          ? '700 15px ' + FONT_BODY
          : '500 12px ' + FONT_BODY;
        note.lines.forEach(function (line, index) {
          ctx.fillText(line, 38, lineY + (note.conclusion ? 20 : 15) + index * (note.conclusion ? 22 : 17));
        });
        lineY += note.conclusion ? 27 + note.lines.length * 22 + 10 : 14 + note.lines.length * 17 + 7;
      });
      detailsY = coachY + layout.coach.height + 24;
    }
    if (layout.detailCount) {
      ctx.fillStyle = palette.text;
      ctx.font = '800 11px system-ui, sans-serif';
      ctx.fillText(viewModel.kind === 'weekly' ? 'QUALITY SESSIONS' : 'TODAY\'S SESSIONS', 20, detailsY);
      ctx.fillStyle = palette.muted;
      ctx.font = '600 10px system-ui, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(String(layout.detailCount).padStart(2, '0'), 355, detailsY);
      ctx.textAlign = 'start';
      ctx.font = '600 12px system-ui, -apple-system, "PingFang SC", sans-serif';
      viewModel.details.slice(0, layout.detailCount).forEach(function (detail, index) {
        var rowY = detailsY + 25 + index * 31;
        ctx.strokeStyle = palette.border;
        ctx.beginPath(); ctx.moveTo(20, rowY + 8); ctx.lineTo(355, rowY + 8); ctx.stroke();
        ctx.fillStyle = palette.performance;
        ctx.beginPath();
        ctx.arc(27, rowY - 4, 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = palette.secondary;
        var label = detail.label + '  ' + (detail.distance ? detail.distance.toFixed(1) + 'km' : '') + (detail.duration ? '  ' + detail.duration : '') + (detail.pace ? '  ' + detail.pace + '/km' : '');
        ctx.fillText(wrapText(ctx, label, 305, 1)[0] || '', 42, rowY);
      });
    }

    drawRule(ctx, palette, layout.height - 42);
    ctx.fillStyle = palette.muted;
    ctx.font = '700 9px system-ui, sans-serif';
    ctx.fillText('NEURUN · KEEP MOVING', 20, layout.height - 20);
    ctx.textAlign = 'right';
    ctx.fillText(viewModel.kind === 'weekly' ? 'WEEKLY ARCHIVE' : 'PERSONAL RUN LOG', 355, layout.height - 20);
    ctx.textAlign = 'start';
  }

  function encodeShareCard(canvas) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (!blob || !blob.size) reject(fail('png_encode_failed', 'PNG 编码失败，请重试。'));
        else resolve(blob);
      }, 'image/png');
    });
  }

  async function createShareCard(options) {
    if (!global.document || !document.createElement) throw fail('canvas_unavailable', '当前浏览器不支持 Canvas。');
    var viewModel = options.type === 'weekly'
      ? buildWeeklyShareCardData(options.report, options.theme)
      : buildDailyShareCardData(options.report, options.theme);
    var canvas = document.createElement('canvas');
    var ctx = canvas.getContext('2d');
    if (!ctx) throw fail('canvas_unavailable', '当前浏览器不支持 Canvas 2D。');
    var layout = measureShareCard(viewModel, ctx);
    canvas.width = LOGICAL_WIDTH * SCALE;
    canvas.height = layout.height * SCALE;
    drawShareCard(viewModel, canvas, layout);
    var blob = await encodeShareCard(canvas);
    canvas.width = 1;
    canvas.height = 1;
    var id = viewModel.date || new Date().toISOString().slice(0, 10);
    return {blob: blob, filename: 'neurun-' + viewModel.kind + '-' + id.replace(/[^0-9-]+/g, '-') + '.png'};
  }

  function download(blob, filename) {
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    global.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  }

  async function shareOrDownload(blob, filename) {
    var file = typeof File === 'function' ? new File([blob], filename, {type: 'image/png'}) : null;
    if (file && navigator.share && navigator.canShare && navigator.canShare({files: [file]})) {
      try {
        await navigator.share({files: [file], title: 'neurun 跑步分享卡'});
        return {status: 'shared'};
      } catch (error) {
        if (error && (error.name === 'AbortError' || error.name === 'NotAllowedError')) return {status: 'cancelled'};
        download(blob, filename);
        return {status: 'downloaded', reason: 'share_failed'};
      }
    }
    download(blob, filename);
    return {status: 'downloaded'};
  }

  global.NeurunShareCard = {
    buildDailyShareCardData: buildDailyShareCardData,
    buildWeeklyShareCardData: buildWeeklyShareCardData,
    createShareCard: createShareCard,
    shareOrDownload: shareOrDownload,
    download: download
  };
})(window);
