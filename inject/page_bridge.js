/**
 * 注入到目标答题页：抓 DOM 题目 + 按 AI 结果自动勾选/填空。
 * 由桌面端通过 Chrome DevTools Protocol Runtime.evaluate 下发。
 */
(function () {
  if (window.__AI_STUDY_BRIDGE__) return window.__AI_STUDY_BRIDGE__;

  const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

  function norm(s) {
    return String(s || "")
      .replace(/\s+/g, " ")
      .replace(/[【】\[\]\(\)（）]/g, "")
      .trim();
  }

  function visible(el) {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || st.opacity === "0") return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  }

  function textOf(el) {
    return norm(el && (el.innerText || el.textContent || ""));
  }

  function fire(el, type) {
    try {
      el.dispatchEvent(new Event(type, { bubbles: true }));
      el.dispatchEvent(new MouseEvent(type === "click" ? "click" : type, { bubbles: true, cancelable: true, view: window }));
    } catch (e) {}
  }

  function reactSet(el, value) {
    const proto = el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    fire(el, "input");
    fire(el, "change");
  }

  function clickEl(el) {
    if (!el) return false;
    const target = el.closest("label") || el;
    try { target.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {}
    const input = target.matches("input,button") ? target : target.querySelector("input[type=radio],input[type=checkbox],button");
    if (input) {
      if (input.type === "checkbox" || input.type === "radio") {
        if (!input.checked) {
          input.checked = true;
          fire(input, "click");
          fire(input, "change");
          fire(input, "input");
        }
      } else {
        input.click();
        fire(input, "click");
      }
    }
    try { target.click(); } catch (e) {}
    fire(target, "click");
    return true;
  }

  function letterFromText(t) {
    const s = String(t || "").replace(/\s+/g, " ").trim();
    const m = s.match(/^([A-Ha-h])(?:[\.．、。:：\)）\s].*)?$/);
    if (m) return m[1].toUpperCase();
    const compact = s.replace(/\s/g, "");
    if (/^[A-Ha-h]$/.test(compact)) return compact.toUpperCase();
    return "";
  }

  function parseChoiceLetters(ans) {
    const raw = String(ans || "").toUpperCase().replace(/\s+/g, "");
    const m = raw.match(/[A-Z]/g);
    if (!m) return [];
    const uniq = [];
    m.forEach((ch) => { if (!uniq.includes(ch)) uniq.push(ch); });
    return uniq;
  }

  function isTrueAnswer(ans) {
    return /(^对$|^正确$|^是$|^true$|^t$|^yes$|^√$|^✓$)/i.test(String(ans).trim());
  }
  function isFalseAnswer(ans) {
    return /(^错$|^错误$|^否$|^false$|^f$|^no$|^×$|^✗$)/i.test(String(ans).trim());
  }

  // 部分平台判断题用 A/B 表示，A=对，B=错
  function judgementToLetter(ans) {
    const s = String(ans || "").trim();
    if (isTrueAnswer(s)) return "A";
    if (isFalseAnswer(s)) return "B";
    return "";
  }

  function optionNodes(root) {
    const scope = root || document;
    const nodes = [];
    scope.querySelectorAll("label, li, .option, [class*='option'], [class*='choice'], [class*='answer']").forEach((el) => {
      if (!visible(el)) return;
      if (el.querySelector("label, li, .option")) return;
      const t = textOf(el);
      if (!t || t.length > 400) return;
      nodes.push(el);
    });
    scope.querySelectorAll("input[type=radio], input[type=checkbox]").forEach((inp) => {
      if (!visible(inp) && !inp.offsetParent) return;
      const lab = inp.closest("label") || (inp.id ? document.querySelector(`label[for="${inp.id}"]`) : null);
      if (lab && !nodes.includes(lab)) nodes.push(lab);
    });
    return nodes;
  }

  function questionBlocks() {
    const selectors = [
      ".question", ".ques", ".topic", ".subject", ".exam-question",
      "[class*='question']", "[class*='Question']", "[class*='timu']",
      "[class*='shiti']", "li.item", ".el-radio-group", "fieldset"
    ];
    const found = [];
    selectors.forEach((sel) => {
      document.querySelectorAll(sel).forEach((el) => {
        if (!visible(el)) return;
        if (el.querySelector(sel) && textOf(el).length > 1200) return;
        const t = textOf(el);
        if (t.length < 6) return;
        if (found.some((x) => x.contains(el) || el.contains(x))) return;
        found.push(el);
      });
    });
    if (found.length >= 1) return found;

    const html = document.body ? document.body.innerText : "";
    return html ? [document.body] : [];
  }

  function extractQuestions() {
    const blocks = questionBlocks();
    const qs = [];
    blocks.forEach((block, i) => {
      const raw = textOf(block);
      if (!raw) return;
      const opts = [];
      optionNodes(block).forEach((el) => {
        const t = textOf(el);
        const letter = letterFromText(t);
        if (letter || /对|错|正确|错误|是|否/.test(t)) {
          opts.push({ letter: letter || "", text: t.slice(0, 200) });
        }
      });
      qs.push({
        index: i + 1,
        stem: raw.slice(0, 800),
        options: opts
      });
    });

    if (qs.length <= 1) {
      const body = (document.body && document.body.innerText) || "";
      const parts = body.split(/(?=^\s*\d+[\.、．]|\n\s*\d+[\.、．])/m).filter((p) => p.trim().length > 8);
      if (parts.length > qs.length) {
        return parts.slice(0, 80).map((p, i) => ({ index: i + 1, stem: p.trim().slice(0, 800), options: [] }));
      }
    }
    return qs;
  }

  function pageText() {
    const clone = document.body ? document.body.cloneNode(true) : null;
    if (!clone) return "";
    clone.querySelectorAll("script,style,noscript,iframe").forEach((n) => n.remove());
    return (clone.innerText || "").trim().slice(0, 80000);
  }

  function matchOption(block, letter, answerText) {
    const nodes = optionNodes(block);
    const want = letter.toUpperCase();
    for (const el of nodes) {
      const t = textOf(el);
      if (letterFromText(t) === want) return el;
    }
    for (const el of nodes) {
      const inp = el.querySelector("input") || (el.matches("input") ? el : null);
      if (inp && (String(inp.value).toUpperCase() === want || String(inp.getAttribute("data-value") || "").toUpperCase() === want)) {
        return el;
      }
    }
    if (answerText) {
      const n = norm(answerText);
      for (const el of nodes) {
        if (norm(textOf(el)).includes(n) && n.length >= 2) return el;
      }
    }
    return null;
  }

  function findJudgement(block, truthy) {
    const nodes = optionNodes(block);
    const pos = /对|正确|是|true|yes|√/i;
    const neg = /错|错误|否|false|no|×/i;
    for (const el of nodes) {
      const t = textOf(el);
      if (truthy && pos.test(t) && !neg.test(t)) return el;
      if (!truthy && neg.test(t)) return el;
    }
    const inputs = block.querySelectorAll("input[type=radio], input[type=checkbox], button");
    for (const inp of inputs) {
      const bag = ((inp.value || "") + " " + (inp.getAttribute("aria-label") || "") + " " + textOf(inp.parentElement)).toLowerCase();
      if (truthy && /true|对|正确/.test(bag)) return inp;
      if (!truthy && /false|错|错误/.test(bag)) return inp;
    }
    return null;
  }

  function fillBlank(block, value) {
    const inputs = [...block.querySelectorAll("input[type=text], input:not([type]), input[type=search], textarea")].filter(visible);
    if (!inputs.length) return false;
    reactSet(inputs[0], String(value || ""));
    return true;
  }

  function visibleOptionRows(root) {
    const scope = root || document;
    const sels = [
      ".choice-item", ".option-item", ".exam-option", ".question-option",
      ".el-radio", ".el-checkbox", ".el-radio-group .el-radio",
      "[class*='choice-item']", "[class*='option-item']",
      "label", "li"
    ];
    const out = [];
    sels.forEach((sel) => {
      scope.querySelectorAll(sel).forEach((el) => {
        if (!visible(el)) return;
        const t = textOf(el);
        if (!t || t.length > 500) return;
        if (!/^[A-H][\.．、。:：\s\)]/.test(t) && !el.querySelector("input[type=radio],input[type=checkbox]")) return;
        if (!out.includes(el)) out.push(el);
      });
    });
    return out;
  }

  function clickLetterInScope(scope, letter) {
    const want = String(letter || "").toUpperCase();
    const rows = visibleOptionRows(scope);
    for (const el of rows) {
      if (letterFromText(textOf(el)) === want) return clickEl(el);
    }
    const all = [...scope.querySelectorAll("span,em,i,div,label,p")].filter(visible);
    for (const el of all) {
      const t = (el.innerText || "").trim();
      if (t === want || t === want + "." || t === want + "、") {
        return clickEl(el.closest("label,li,.el-radio,.el-checkbox,.choice-item") || el);
      }
    }
    return false;
  }

  function currentQuestionNo() {
    const body = textOf(document.body);
    const m = body.match(/(\d+)\s*\/\s*\d+/) || body.match(/第\s*(\d+)\s*题/);
    if (m) return Number(m[1]);
    const active = document.querySelector(".ques-num.active, .question-index.active, [class*='current']");
    const n = active && (active.innerText || "").match(/\d+/);
    return n ? Number(n[0]) : 1;
  }

  function clickNextQuestion() {
    const btns = [...document.querySelectorAll("button, a, span, div")].filter(visible);
    const hit = btns.find((el) => /下一题|下一页|下一道/.test((el.innerText || "").trim()));
    if (hit) {
      clickEl(hit);
      return true;
    }
    return false;
  }

  function applyCurrentQuestion(item) {
    const ans = String((item && item.answer) || "").trim();
    const letters = parseChoiceLetters(ans);
    const scope = document.querySelector(".question-content, .exam-question, .ques-content, .question-box") || document.body;
    let ok = false;
    letters.forEach((L) => { if (clickLetterInScope(scope, L)) ok = true; });
    return { index: item && item.index, answer: ans, ok, letters };
  }

  async function applyAnswersPaged(answers, delayMs) {
    const list = Array.isArray(answers) ? answers : [];
    const report = { total: list.length, applied: 0, missed: [], details: [], mode: "paged" };
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const delay = Math.max(250, Number(delayMs) || 450);
    for (let i = 0; i < list.length; i++) {
      const item = list[i];
      const cur = applyCurrentQuestion(item);
      if (cur.ok) report.applied += 1;
      else report.missed.push(item.index || i + 1);
      report.details.push(cur);
      if (i < list.length - 1) {
        const moved = clickNextQuestion();
        await wait(delay);
        if (!moved) {
          report.note = "没找到「下一题」按钮，只处理了当前可见题";
          break;
        }
      }
    }
    return report;
  }

  function optionLetterOf(el) {
    const raw = (el.innerText || el.textContent || "").replace(/\s+/g, "").toUpperCase();
    if (/^[A-H]$/.test(raw)) return raw;
    if (/^[A-H][\.．、]/.test(raw)) return raw[0];
    const em = el.querySelector && el.querySelector("em");
    if (em) {
      const et = (em.innerText || "").replace(/\s+/g, "").toUpperCase();
      if (/^[A-H]$/.test(et)) return et;
    }
    return letterFromText(el.innerText || "");
  }

  function findLetterControl(root, letter) {
    const L = String(letter || "").toUpperCase();
    const preferred = [
      "div.answer .btns button.sel",
      "div.answer button.sel",
      ".btns button.sel",
      "button.sel"
    ];
    for (const sel of preferred) {
      const hit = [...root.querySelectorAll(sel)].find((el) => optionLetterOf(el) === L);
      if (hit) return hit;
    }
    return [...root.querySelectorAll(".btns button")].find((el) => optionLetterOf(el) === L) || null;
  }

  function selectedLetters(root) {
    const out = [];
    [...root.querySelectorAll("button.sel.cur")].forEach((el) => {
      const L = optionLetterOf(el);
      if (L && !out.includes(L)) out.push(L);
    });
    return out;
  }

  function findExamVue(node) {
    let cur = node;
    while (cur) {
      const v = cur.__vue__;
      if (v && typeof v.goSelect === "function") return v;
      if (v && v.$parent && typeof v.$parent.goSelect === "function") return v.$parent;
      cur = cur.parentElement;
    }
    const nodes = document.querySelectorAll(".practiceing, .questionBank, .content, #app");
    for (const el of nodes) {
      const v = el.__vue__;
      if (v && typeof v.goSelect === "function") return v;
      if (v && v.$parent && typeof v.$parent.goSelect === "function") return v.$parent;
    }
    return null;
  }

  function vueSelect(root, letter, qIndex) {
    const L = String(letter || "").toUpperCase();
    const vue = findExamVue(root);
    if (!vue || typeof vue.goSelect !== "function") return false;
    const list = vue.questionsList || vue.combinedQuestions || [];
    const q = list.find((x) => Number(x.OrderIndex) === Number(qIndex)) || list[Number(qIndex) - 1];
    if (!q) return false;
    // 单选/多选走 ChoiceItems，判断题走 JudgeItems（A=正确 B=错误）
    const pool = [
      ...(Array.isArray(q.ChoiceItems) ? q.ChoiceItems : []),
      ...(Array.isArray(q.JudgeItems) ? q.JudgeItems : [])
    ];
    const item = pool.find((x) => String(x.ItemCode || "").toUpperCase() === L);
    if (!item) return false;
    try { vue.goSelect(q, item); return true; } catch (e1) {}
    try { vue.goSelect(q, item, qIndex); return true; } catch (e2) {}
    return false;
  }

  function nativeClick(el) {
    try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {}
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((type) => {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, buttons: 1 }));
    });
    try { el.click(); } catch (e) {}
  }

  function clickSelButtons(scope, letters) {
    const root = scope || document;
    let any = false;
    const hits = [];
    letters.forEach((L) => {
      const hit = findLetterControl(root, L);
      if (hit) {
        try { hit.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {}
        hit.click();
        fire(hit, "click");
        any = true;
        hits.push(L);
      }
    });
    return { ok: any, hits };
  }

  function examQuestionBlocks() {
    const byId = [...document.querySelectorAll("[id^='question']")];
    if (byId.length) return byId;
    const answers = [...document.querySelectorAll("div.answer")];
    if (answers.length) return answers.map((a) => a.parentElement || a);
    return [];
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function applyCcbAnswers(answers) {
    const list = Array.isArray(answers) ? answers : [];
    const report = { total: list.length, applied: 0, missed: [], details: [], mode: "exam" };
    for (let i = 0; i < list.length; i++) {
      const item = list[i];
      let letters = parseChoiceLetters(item.answer);
      // 判断题：对→A/正确，错→B/错误
      if (!letters.length && (isTrueAnswer(item.answer) || isFalseAnswer(item.answer))) {
        const L = judgementToLetter(item.answer);
        if (L) letters = [L];
        item.__judgeText = isTrueAnswer(item.answer) ? "正确" : "错误";
      }
      const blocks = examQuestionBlocks();
      report.blocks = blocks.length;
      const idx = Number(item.index || i + 1) - 1;
      const block = blocks[idx] || blocks[i];
      const root = block || document;
      const qNo = Number(item.index || i + 1);
      const isMulti = letters.length > 1 || /多选|multi/i.test(item.type || "");
      const gap = isMulti ? 420 : 160;
      for (const L of letters) {
        const viaVue = vueSelect(root, L, qNo);
        if (!viaVue) {
          let el = findLetterControl(root, L);
          // 判断题按钮可能显示「正确/错误」而不是 A/B
          if (!el && item.__judgeText) {
            const want = item.__judgeText;
            el = [...root.querySelectorAll("button, label, span, div")].find((n) => {
              const t = (n.innerText || "").trim();
              return t === want || t === want + "。" || /^[AB][.．、]?\s*/.test(t) && t.includes(want);
            });
          }
          if (el) nativeClick(el);
        }
        await sleep(gap);
      }
      for (let round = 0; round < 3; round++) {
        const have = selectedLetters(root);
        const missing = letters.filter((L) => !have.includes(L));
        if (!missing.length) break;
        for (const L of missing) {
          if (!vueSelect(root, L, qNo)) {
            const el = findLetterControl(root, L);
            if (el) nativeClick(el);
          }
          await sleep(gap);
        }
      }
      const hitLetters = selectedLetters(root);
      const need = letters.length;
      const ok = need > 0 && letters.every((L) => hitLetters.includes(L));
      if (ok) report.applied += 1;
      else report.missed.push(item.index || i + 1);
      report.details.push({
        index: item.index || i + 1,
        type: item.type,
        answer: item.answer,
        letters,
        hitLetters,
        ok
      });
    }
    return report;
  }

  function applyAnswers(answers) {
    const list = Array.isArray(answers) ? answers : [];
    if (examQuestionBlocks().length || document.querySelector("button.sel") || document.querySelector("div.cards")) {
      return applyCcbAnswers(list);
    }
    const rows = visibleOptionRows(document);
    if (rows.length && rows.length <= 8 && list.length > 1) {
      return applyAnswersPaged(list);
    }
    const blocks = questionBlocks();
    const report = { total: list.length, applied: 0, missed: [], details: [], mode: "all" };

    list.forEach((item, i) => {
      const idx = Number(item.index || i + 1) - 1;
      const block = blocks[idx] || blocks[0];
      if (!block) {
        report.missed.push(item.index || i + 1);
        return;
      }
      const type = String(item.type || "");
      const ans = String(item.answer || "").trim();
      let ok = false;

      if (/判断|true|false/i.test(type) || isTrueAnswer(ans) || isFalseAnswer(ans)) {
        const truthy = isTrueAnswer(ans) || /^对|正确|true/i.test(ans);
        const el = findJudgement(block, truthy);
        ok = clickEl(el);
        // 页面用 A/B 表示判断时，对=A 错=B
        if (!ok) {
          const L = judgementToLetter(ans);
          if (L) {
            const hit = matchOption(block, L, ans) || findLetterControl(block, L);
            if (hit) ok = clickEl(hit);
          }
        }
      } else if (/填空|fill/i.test(type) || (!/单选|多选|选择/.test(type) && !parseChoiceLetters(ans).length)) {
        if (/单选|多选|选择/.test(type)) {
          /* fallthrough to letters */
        } else if (ans && !/^[A-Z]{1,8}$/.test(ans.replace(/\s/g, "").toUpperCase())) {
          ok = fillBlank(block, ans);
        }
      }

      if (!ok) {
        const letters = parseChoiceLetters(ans);
        if (letters.length) {
          let any = false;
          letters.forEach((L) => {
            const el = matchOption(block, L, ans);
            if (clickEl(el)) any = true;
          });
          ok = any;
        }
      }

      if (ok) report.applied += 1;
      else report.missed.push(item.index || i + 1);
      report.details.push({ index: item.index || i + 1, answer: ans, ok });
    });
    return report;
  }

  window.__AI_STUDY_BRIDGE__ = {
    extractQuestions,
    pageText,
    applyAnswers,
    applyCcbAnswers,
    applyAnswersPaged,
    applyCurrentQuestion,
    currentQuestionNo,
    optionNodes,
    questionBlocks
  };
  return window.__AI_STUDY_BRIDGE__;
})();
