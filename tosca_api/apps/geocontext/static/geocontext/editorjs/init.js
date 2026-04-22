/*
 * GeoContext Editor.js admin enhancement.
 *
 * Progressive enhancement: every <textarea data-editorjs-target> is hidden
 * and replaced with an Editor.js instance. Block data is mirrored back to
 * the textarea on every save and again on form submit so the standard
 * Django POST carries canonical JSON.
 *
 * If Editor.js fails to load (missing vendor asset, runtime error, JS
 * disabled), the raw JSON textarea remains visible and fully editable as
 * a no-JS fallback.
 */
(function () {
    "use strict";

    function sanitizeForStorage(output) {
        var blocks = (output && Array.isArray(output.blocks)) ? output.blocks : [];
        var cleaned = blocks.map(function (block) {
            var data = block && block.data ? Object.assign({}, block.data) : {};
            if (block.type === "quote") {
                delete data.alignment;
            }
            return { type: block.type, data: data };
        });
        return { blocks: cleaned };
    }

    function parseInitial(textarea) {
        const raw = (textarea.value || "").trim();
        if (!raw) return { blocks: [] };
        try {
            const parsed = JSON.parse(raw);
            if (parsed && Array.isArray(parsed.blocks)) return parsed;
        } catch (e) {
            // Fall through to empty doc — user can still edit the textarea.
        }
        return { blocks: [] };
    }

    function mount(textarea) {
        if (typeof EditorJS === "undefined") return;

        const holder = document.createElement("div");
        holder.className = "geocontext-editorjs-holder";
        textarea.parentNode.insertBefore(holder, textarea);
        textarea.classList.add("geocontext-editorjs-textarea-hidden");

        const tools = {};
        if (typeof Header !== "undefined") {
            tools.header = {
                class: Header,
                config: {
                    levels: [1, 2, 3, 4],
                    defaultLevel: 2,
                    toolbox: [
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 1", data: { level: 1 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 2", data: { level: 2 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 3", data: { level: 3 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 4", data: { level: 4 } },
                    ],
                },
            };
        }
        if (typeof List !== "undefined") tools.list = { class: List, inlineToolbar: true };
        if (typeof Quote !== "undefined") tools.quote = { class: Quote, inlineToolbar: true };
        if (typeof Delimiter !== "undefined") tools.delimiter = Delimiter;
        if (typeof CodeTool !== "undefined") tools.code = CodeTool;

        const editor = new EditorJS({
            holder: holder,
            data: parseInitial(textarea),
            tools: tools,
            placeholder: "Write content…",
            onChange: function () {
                editor.save().then(function (output) {
                    textarea.value = JSON.stringify(sanitizeForStorage(output));
                }).catch(function () { /* leave previous textarea value */ });
            },
        });

        function findCurrentListItem() {
            const sel = window.getSelection();
            if (!sel || sel.rangeCount === 0) return null;
            let node = sel.anchorNode;
            if (!node) return null;
            if (node.nodeType === Node.TEXT_NODE) node = node.parentNode;
            if (!node || !node.closest) return null;
            return node.closest(".cdx-list__item");
        }

        function isEmptyItem(li) {
            const html = (li.innerHTML || "")
                .replace(/<br\s*\/?>/gi, "")
                .replace(/&nbsp;/gi, "")
                .replace(/\u00a0/g, "")
                .trim();
            return html === "";
        }

        holder.addEventListener("keydown", function (event) {
            if (event.key !== "Backspace") return;

            const li = findCurrentListItem();
            if (!li || !isEmptyItem(li)) return;

            const list = li.parentElement;
            if (!list || list.querySelectorAll(".cdx-list__item").length < 2) return;

            const prev = li.previousElementSibling;
            if (!prev) return;

            event.preventDefault();
            event.stopPropagation();
            li.remove();

            const range = document.createRange();
            range.selectNodeContents(prev);
            range.collapse(false);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            if (typeof prev.focus === "function") prev.focus();

            editor.save().then(function (output) {
                textarea.value = JSON.stringify(sanitizeForStorage(output));
            }).catch(function () {});
        }, true);

        holder.addEventListener("keydown", function (event) {
            if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
            const li = findCurrentListItem();
            if (!li) return;
            const sibling = event.key === "ArrowUp"
                ? li.previousElementSibling
                : li.nextElementSibling;
            if (!sibling || !sibling.classList.contains("cdx-list__item")) return;
            event.preventDefault();
            const range = document.createRange();
            range.selectNodeContents(sibling);
            range.collapse(event.key === "ArrowDown");
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            if (typeof sibling.focus === "function") sibling.focus();
        }, true);

        const form = textarea.closest("form");
        if (form) {
            form.addEventListener("submit", function (event) {
                event.preventDefault();
                editor.save().then(function (output) {
                    textarea.value = JSON.stringify(sanitizeForStorage(output));
                    form.submit();
                }).catch(function () {
                    form.submit();
                });
            }, { once: true });
        }
    }

    function init() {
        const targets = document.querySelectorAll("textarea[data-editorjs-target]");
        targets.forEach(mount);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
