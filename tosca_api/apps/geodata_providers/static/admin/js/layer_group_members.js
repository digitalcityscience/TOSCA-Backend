/* Keep LayerGroupMember inline defaults aligned with bottom-to-top map order. */
window.addEventListener('load', function () {
    (function ($) {
        if (!$) return;

        var prefix = 'members';

        function visibleRows() {
            return $('.dynamic-' + prefix).filter(function () {
                var deleteInput = $(this).find('input[name$="-DELETE"]');
                return !deleteInput.prop('checked') && $(this).is(':visible');
            });
        }

        function updateOrders() {
            var rows = visibleRows().get();
            rows.sort(function (left, right) {
                var leftOrder = parseInt($(left).find('input[name$="-order"]').val(), 10) || 0;
                var rightOrder = parseInt($(right).find('input[name$="-order"]').val(), 10) || 0;
                return leftOrder - rightOrder;
            });
            rows.forEach(function (row, index) {
                $(row).find('input[name$="-order"]').val(index);
            });
        }

        function moveRowToOrder($row) {
            var rows = visibleRows().get();
            var row = $row.get(0);
            var desiredOrder = parseInt($row.find('input[name$="-order"]').val(), 10);
            if (Number.isNaN(desiredOrder)) desiredOrder = 0;
            desiredOrder = Math.max(0, Math.min(desiredOrder, rows.length - 1));

            var otherRows = rows.filter(function (candidate) {
                return candidate !== row;
            });
            otherRows.sort(function (left, right) {
                var leftOrder = parseInt($(left).find('input[name$="-order"]').val(), 10) || 0;
                var rightOrder = parseInt($(right).find('input[name$="-order"]').val(), 10) || 0;
                return leftOrder - rightOrder;
            });
            otherRows.splice(desiredOrder, 0, row);
            otherRows.forEach(function (candidate, index) {
                $(candidate).find('input[name$="-order"]').val(index);
            });
        }

        function assignmentOptions($select) {
            var cached = $select.data('layer-assignment-options');
            if (cached) return cached;
            cached = $select.find('option').clone();
            $select.data('layer-assignment-options', cached);
            return cached;
        }

        function filterAssignments($row, chooseDefault) {
            var layerId = String($row.find('select[name$="-layer"]').val() || '');
            var $select = $row.find('select[name$="-style_assignment"]');
            if (!$select.length) return;
            var currentValue = chooseDefault ? '' : String($select.val() || '');
            var options = assignmentOptions($select);
            $select.empty();
            options.each(function () {
                var $option = $(this);
                var optionLayerId = String($option.attr('data-layer-id') || '');
                if (!optionLayerId || optionLayerId === layerId) {
                    $select.append($option.clone());
                }
            });

            if (currentValue && $select.find('option[value="' + currentValue + '"]').length) {
                $select.val(currentValue);
            } else if (layerId) {
                var defaultValue = $select.find('option[data-role="default"]').first().val();
                $select.val(defaultValue || '');
            } else {
                $select.val('');
            }
            $select.trigger('change');
        }

        function updateSourcePreview($row) {
            var layerLabel = $row.find('select[name$="-layer"] option:selected').text().trim();
            var layerName = layerLabel.split('/').pop();
            if (!layerName || !layerLabel) layerName = 'Generated when saved';
            $row.find('.field-source_alias_display p').html(
                '<code>' + $('<div>').text(layerName).html() + '</code><br>' +
                '<small style="color:var(--body-quiet-color);">' +
                'Member key; repeated vector data shares one runtime source automatically.</small>'
            );
        }

        function renderLayerIds($row) {
            var rawValue = String($row.find('[name$="-render_layer_ids"]').val() || '[]');
            try {
                var parsed = JSON.parse(rawValue);
                return Array.isArray(parsed) ? parsed : [];
            } catch (_error) {
                return [];
            }
        }

        function initializeRow($row, chooseDefault) {
            var layerId = String($row.find('select[name$="-layer"]').val() || '');
            var previousLayerId = String($row.data('initialized-layer-id') || '');
            // Django autocomplete emits both change and select2:select. Only
            // the first event for a genuinely new layer may choose a default;
            // the delayed duplicate must preserve the user's style selection.
            filterAssignments($row, chooseDefault && layerId !== previousLayerId);
            $row.data('initialized-layer-id', layerId);
            updateSourcePreview($row);
        }

        function submittedMembers() {
            return visibleRows().map(function () {
                var $row = $(this);
                var layerId = String($row.find('select[name$="-layer"]').val() || '');
                if (!layerId) return null;
                return {
                    id: String($row.find('input[name$="-id"]').val() || ''),
                    layer_id: layerId,
                    style_assignment_id: String(
                        $row.find('select[name$="-style_assignment"]').val() || ''
                    ),
                    title: String($row.find('input[name$="-title"]').val() || ''),
                    render_layer_ids: renderLayerIds($row),
                    order: parseInt($row.find('input[name$="-order"]').val(), 10) || 0
                };
            }).get().filter(Boolean);
        }

        function warningEndpoint() {
            return window.location.pathname.replace(
                /layergroup\/.*$/,
                'layergroup/composition-warnings/'
            );
        }

        function confirmWarnings(warnings, onConfirm, onClose) {
            var proceeding = false;
            var dialog = document.createElement('dialog');
            dialog.setAttribute('aria-labelledby', 'layer-group-warning-title');
            dialog.style.maxWidth = '680px';
            dialog.style.border = '1px solid var(--hairline-color)';
            dialog.style.borderRadius = '4px';
            dialog.style.padding = '24px';

            var title = document.createElement('h2');
            title.id = 'layer-group-warning-title';
            title.textContent = 'Layer group warning';
            title.style.marginTop = '0';
            dialog.appendChild(title);

            var intro = document.createElement('p');
            intro.textContent = 'Review these warnings before saving:';
            dialog.appendChild(intro);

            var list = document.createElement('ul');
            warnings.forEach(function (warning) {
                var item = document.createElement('li');
                item.textContent = warning;
                list.appendChild(item);
            });
            dialog.appendChild(list);

            var note = document.createElement('p');
            note.textContent = 'This warning does not block publishing. Do you want to save anyway?';
            dialog.appendChild(note);

            var actions = document.createElement('div');
            actions.style.display = 'flex';
            actions.style.justifyContent = 'flex-end';
            actions.style.gap = '10px';

            var cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.textContent = 'Review group';
            cancel.addEventListener('click', function () {
                dialog.close();
            });
            actions.appendChild(cancel);

            var proceed = document.createElement('button');
            proceed.type = 'button';
            proceed.className = 'default';
            proceed.textContent = 'Save layer group anyway';
            proceed.addEventListener('click', function () {
                proceeding = true;
                dialog.close();
                onConfirm();
            });
            actions.appendChild(proceed);
            dialog.appendChild(actions);

            dialog.addEventListener('close', function () {
                dialog.remove();
                if (!proceeding) onClose();
            });
            document.body.appendChild(dialog);
            dialog.showModal();
        }

        $('.dynamic-' + prefix).each(function () {
            initializeRow($(this), false);
        });

        $(document).on('change', '.dynamic-' + prefix + ' select[name$="-layer"]', function () {
            initializeRow($(this).closest('.dynamic-' + prefix), true);
        });

        $(document).on('select2:select', '.dynamic-' + prefix + ' select[name$="-layer"]', function () {
            var $row = $(this).closest('.dynamic-' + prefix);
            window.setTimeout(function () {
                initializeRow($row, true);
            }, 0);
        });

        $(document).on('formset:added', function (event, $row, formsetName) {
            if (formsetName !== prefix) return;
            initializeRow($row || $(event.target), true);
            ($row || $(event.target)).find('input[name$="-order"]').val(
                visibleRows().length - 1
            );
            updateOrders();
        });

        $(document).on('formset:removed', function (event, $row, formsetName) {
            if (formsetName === prefix) updateOrders();
        });

        $(document).on('change', '.dynamic-' + prefix + ' input[name$="-DELETE"]', updateOrders);
        $(document).on('change', '.dynamic-' + prefix + ' input[name$="-order"]', function () {
            moveRowToOrder($(this).closest('.dynamic-' + prefix));
        });

        var $groupForm = $('#layergroup_form');
        $groupForm.attr('data-editorjs-submit-managed', 'true');

        var saveCheckPassed = false;
        var saveCheckRunning = false;

        function submitAfterWarningCheck(form, submitter) {
            saveCheckPassed = true;

            if (submitter && submitter.name) {
                var actionInput = document.createElement('input');
                actionInput.type = 'hidden';
                actionInput.name = submitter.name;
                actionInput.value = submitter.value || '';
                form.appendChild(actionInput);
            }
            HTMLFormElement.prototype.submit.call(form);
        }

        $groupForm.on('submit', function (event) {
            if (saveCheckPassed) return;
            if (saveCheckRunning) {
                event.preventDefault();
                return;
            }
            event.preventDefault();
            saveCheckRunning = true;

            var form = this;
            var submitter = event.originalEvent && event.originalEvent.submitter;
            var csrfToken = $(form).find('input[name="csrfmiddlewaretoken"]').val();
            var groupIdMatch = window.location.pathname.match(/layergroup\/([^/]+)\/change\//);
            var legendInput = $(form).find('input[type="file"][name="legend_image"]').get(0);
            var editorSave = typeof form.saveEditorJsFields === 'function'
                ? form.saveEditorJsFields()
                : Promise.resolve();

            editorSave.then(function () {
                return fetch(warningEndpoint(), {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken
                    },
                    body: JSON.stringify({
                        members: submittedMembers(),
                        group_id: groupIdMatch === null ? null : groupIdMatch[1],
                        legend_will_refresh: Boolean(legendInput && legendInput.files.length),
                        legend_will_be_removed: $(form)
                            .find('input[name="legend_image-clear"]')
                            .prop('checked') === true,
                        legend_is_confirmed: $(form)
                            .find('input[name="confirm_legend_current"]')
                            .prop('checked') === true
                    })
                });
            }).then(function (response) {
                if (!response.ok) throw new Error('Warning check failed.');
                return response.json();
            }).then(function (result) {
                function submitForm() {
                    saveCheckRunning = false;
                    submitAfterWarningCheck(form, submitter);
                }
                if (result.warnings && result.warnings.length) {
                    confirmWarnings(result.warnings, submitForm, function () {
                        saveCheckRunning = false;
                        if (typeof form.releaseEditorJsSubmit === 'function') {
                            form.releaseEditorJsSubmit();
                        }
                    });
                } else {
                    submitForm();
                }
            }).catch(function () {
                saveCheckPassed = false;
                saveCheckRunning = false;
                if (typeof form.releaseEditorJsSubmit === 'function') {
                    form.releaseEditorJsSubmit();
                }
                window.alert(
                    'The layer group could not be prepared for saving. ' +
                    'Your changes remain on this page; please try again.'
                );
            });
        });
    })(django.jQuery);
});
