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

    function replaceInlineTags(value, replacements) {
        if (typeof value !== "string" || value === "") return value;
        return replacements.reduce(function (result, replacement) {
            return result
                .replace(replacement.open, replacement.openWith)
                .replace(replacement.close, replacement.closeWith);
        }, value);
    }

    function transformListItems(items, transformInline) {
        if (!Array.isArray(items)) return items;
        return items.map(function (item) {
            if (typeof item === "string") return transformInline(item);
            if (!item || typeof item !== "object") return item;
            return Object.assign({}, item, {
                content: transformInline(item.content || ""),
                items: transformListItems(item.items || [], transformInline),
            });
        });
    }

    function transformBlockInlineMarkup(block, transformInline) {
        var data = block && block.data ? Object.assign({}, block.data) : {};
        if (block.type === "paragraph" || block.type === "header") {
            data.text = transformInline(data.text || "");
        } else if (block.type === "list") {
            data.items = transformListItems(data.items || [], transformInline);
        } else if (block.type === "quote") {
            data.text = transformInline(data.text || "");
            data.caption = transformInline(data.caption || "");
        } else if (block.type === "image") {
            data.caption = transformInline(data.caption || "");
        }
        return { type: block.type, data: data };
    }

    function canonicalizeInlineMarkup(value) {
        return replaceInlineTags(value, [
            {
                open: /<b\b[^>]*>/gi,
                close: /<\/b\s*>/gi,
                openWith: "<strong>",
                closeWith: "</strong>",
            },
            {
                open: /<i\b[^>]*>/gi,
                close: /<\/i\s*>/gi,
                openWith: "<em>",
                closeWith: "</em>",
            },
        ]);
    }

    function prepareInlineMarkupForEditor(value) {
        return replaceInlineTags(value, [
            {
                open: /<strong\b[^>]*>/gi,
                close: /<\/strong\s*>/gi,
                openWith: "<b>",
                closeWith: "</b>",
            },
            {
                open: /<em\b[^>]*>/gi,
                close: /<\/em\s*>/gi,
                openWith: "<i>",
                closeWith: "</i>",
            },
        ]);
    }

    function sanitizeForStorage(output) {
        var blocks = (output && Array.isArray(output.blocks)) ? output.blocks : [];
        var cleaned = blocks.map(function (block) {
            var cleanedBlock = transformBlockInlineMarkup(block, canonicalizeInlineMarkup);
            var data = cleanedBlock.data;
            if (block.type === "quote") {
                delete data.alignment;
            }
            return cleanedBlock;
        });
        return { blocks: cleaned };
    }

    function parseInitial(textarea) {
        const raw = (textarea.value || "").trim();
        if (!raw) return { blocks: [] };
        try {
            const parsed = JSON.parse(raw);
            if (parsed && Array.isArray(parsed.blocks)) {
                return {
                    blocks: parsed.blocks.map(function (block) {
                        return transformBlockInlineMarkup(
                            block,
                            prepareInlineMarkupForEditor
                        );
                    }),
                };
            }
        } catch (e) {
            // Fall through to empty doc — user can still edit the textarea.
        }
        return { blocks: [] };
    }

    function getCSRFToken() {
        const input = document.querySelector("input[name=csrfmiddlewaretoken]");
        if (input) return input.value;
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    function submitNativelyPreservingAction(form, submitter) {
        if (submitter && submitter.name) {
            const actionInput = document.createElement("input");
            actionInput.type = "hidden";
            actionInput.name = submitter.name;
            actionInput.value = submitter.value || "";
            form.appendChild(actionInput);
        }
        HTMLFormElement.prototype.submit.call(form);
    }

    function registerFormEditor(form, editor, textarea) {
        let state = form._editorJsSubmitState;
        if (!state) {
            state = { fields: [], submitting: false };
            form._editorJsSubmitState = state;
            form.saveEditorJsFields = function () {
                state.submitting = true;
                return Promise.all(state.fields.map(function (field) {
                    const revision = ++field.saveRevision;
                    return field.editor.isReady.then(function () {
                        return field.editor.save();
                    }).then(function (output) {
                        // Only the newest save may update the submitted field.
                        // Older onChange saves can resolve after this final save.
                        if (revision === field.saveRevision) {
                            field.textarea.value = JSON.stringify(sanitizeForStorage(output));
                        }
                    });
                }));
            };
            form.releaseEditorJsSubmit = function () {
                state.submitting = false;
            };

            form.addEventListener("submit", function (event) {
                // Forms with another asynchronous pre-save check own the full
                // sequence and call saveEditorJsFields before they continue.
                if (form.dataset.editorjsSubmitManaged === "true") return;
                event.preventDefault();
                if (state.submitting) return;
                const submitter = event.submitter;
                form.saveEditorJsFields().then(function () {
                    // The original submit event has already passed native form
                    // validation. Submit exactly once with the finalized JSON,
                    // without dispatching another competing submit event.
                    submitNativelyPreservingAction(form, submitter);
                }).catch(function () {
                    form.releaseEditorJsSubmit();
                    window.alert(
                        "The rich-text content could not be prepared for saving. " +
                        "Your changes remain on this page; please try again."
                    );
                });
            });
        }
        const field = {
            editor: editor,
            textarea: textarea,
            saveRevision: 0,
        };
        state.fields.push(field);
        return field;
    }

    function buildLibraryButton(editor) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "geocontext-editorjs-library-button";
        button.textContent = "Insert image from library";
        button.addEventListener("click", function () {
            openLibraryPicker(editor);
        });
        return button;
    }

    function openLibraryPicker(editor) {
        const overlay = document.createElement("div");
        overlay.className = "geocontext-editorjs-library-overlay";
        const panel = document.createElement("div");
        panel.className = "geocontext-editorjs-library-panel";
        panel.innerHTML = '<div class="geocontext-editorjs-library-header">'
            + '<strong>Choose an existing image</strong>'
            + '<button type="button" class="geocontext-editorjs-library-close">×</button>'
            + '</div>'
            + '<div class="geocontext-editorjs-library-grid">Loading…</div>';
        overlay.appendChild(panel);
        document.body.appendChild(overlay);

        function close() {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        }
        overlay.addEventListener("click", function (e) {
            if (e.target === overlay) close();
        });
        panel.querySelector(".geocontext-editorjs-library-close")
            .addEventListener("click", close);

        fetch("/api/v1/geocontext/editorjs/media/", {
            credentials: "same-origin",
            headers: { "X-CSRFToken": getCSRFToken() },
        })
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                const grid = panel.querySelector(".geocontext-editorjs-library-grid");
                grid.innerHTML = "";
                const items = (payload && payload.results) || [];
                if (items.length === 0) {
                    grid.textContent = "No uploaded images yet.";
                    return;
                }
                items.forEach(function (item) {
                    const tile = document.createElement("button");
                    tile.type = "button";
                    tile.className = "geocontext-editorjs-library-tile";
                    const img = document.createElement("img");
                    img.src = item.url;
                    img.alt = item.name;
                    tile.appendChild(img);
                    tile.addEventListener("click", function () {
                        editor.blocks.insert("image", {
                            file: {
                                url: item.url,
                                mime: item.mime,
                                width: item.width,
                                height: item.height,
                            },
                            alt: item.name || "image",
                            caption: "",
                            withBorder: false,
                            withBackground: false,
                            stretched: false,
                        });
                        close();
                    });
                    grid.appendChild(tile);
                });
            })
            .catch(function () {
                panel.querySelector(".geocontext-editorjs-library-grid").textContent =
                    "Failed to load library.";
            });
    }

    function mount(textarea) {
        if (typeof EditorJS === "undefined") return;

        const profile = textarea.dataset.editorjsProfile || "full";
        const isDescription = profile === "description";
        const form = textarea.closest("form");

        const holder = document.createElement("div");
        holder.className = "geocontext-editorjs-holder";
        textarea.parentNode.insertBefore(holder, textarea);
        textarea.classList.add("geocontext-editorjs-textarea-hidden");

        const tools = {};
        if (typeof Header !== "undefined") {
            tools.header = {
                class: Header,
                config: {
                    levels: isDescription ? [2, 3, 4] : [1, 2, 3, 4],
                    defaultLevel: 2,
                    toolbox: (isDescription ? [
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 2", data: { level: 2 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 3", data: { level: 3 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 4", data: { level: 4 } },
                    ] : [
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 1", data: { level: 1 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 2", data: { level: 2 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 3", data: { level: 3 } },
                        { icon: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 4v16M20 4v16M4 12h16"/></svg>', title: "Heading 4", data: { level: 4 } },
                    ]),
                },
            };
        }
        const ListClass = (typeof EditorjsList !== "undefined") ? EditorjsList
            : (typeof List !== "undefined") ? List
            : null;
        if (ListClass) tools.list = { class: ListClass, inlineToolbar: true };
        if (!isDescription && typeof Quote !== "undefined") tools.quote = { class: Quote, inlineToolbar: true };
        if (!isDescription && typeof Delimiter !== "undefined") tools.delimiter = Delimiter;
        if (!isDescription && typeof CodeTool !== "undefined") tools.code = CodeTool;
        if (!isDescription && typeof ImageTool !== "undefined") {
            tools.image = {
                class: ImageTool,
                config: {
                    endpoints: {
                        byFile: "/api/v1/geocontext/editorjs/upload-by-file/",
                        byUrl: "/api/v1/geocontext/editorjs/upload-by-url/",
                    },
                    field: "image",
                    additionalRequestHeaders: {
                        "X-CSRFToken": getCSRFToken(),
                    },
                },
            };
        }

        let registeredField;
        const editor = new EditorJS({
            holder: holder,
            data: parseInitial(textarea),
            tools: tools,
            // Editor.js' built-in bold and italic tools author <b>/<i>, while
            // the backend stores the semantic <strong>/<em> equivalents.
            // Permit both representations so a saved document can be loaded,
            // edited, and saved again without losing inline formatting.
            sanitizer: {
                a: { href: true, title: true },
                b: true,
                strong: true,
                i: true,
                em: true,
                code: true,
                br: true,
            },
            placeholder: isDescription ? "Write a clear public description…" : "Write content…",
            onChange: function () {
                const state = form && form._editorJsSubmitState;
                if (!registeredField || (state && state.submitting)) return;
                const revision = ++registeredField.saveRevision;
                editor.save().then(function (output) {
                    if (
                        revision === registeredField.saveRevision
                        && !(state && state.submitting)
                    ) {
                        textarea.value = JSON.stringify(sanitizeForStorage(output));
                    }
                }).catch(function () { /* leave previous textarea value */ });
            },
        });

        if (!isDescription && typeof ImageTool !== "undefined") {
            holder.parentNode.insertBefore(buildLibraryButton(editor), holder);
        }

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

            if (!registeredField) return;
            const state = form && form._editorJsSubmitState;
            if (state && state.submitting) return;
            const revision = ++registeredField.saveRevision;
            editor.save().then(function (output) {
                if (
                    revision === registeredField.saveRevision
                    && !(state && state.submitting)
                ) {
                    textarea.value = JSON.stringify(sanitizeForStorage(output));
                }
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

        if (form) registeredField = registerFormEditor(form, editor, textarea);
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
