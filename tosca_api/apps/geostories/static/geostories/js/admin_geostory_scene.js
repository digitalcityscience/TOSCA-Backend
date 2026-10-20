/* Scene layer inline: keep style options scoped to the chosen layer and
 * display_order sequential (0 = bottom). */
window.addEventListener('load', function () {
    (function ($) {
        if (!$) return;

        var prefix = 'scene_layers';
        var simulationRowSelector = '#simulation-group .inline-related:not(.empty-form)';
        var editorConfigElement = document.getElementById('scene-editor-config');
        var editorConfig = editorConfigElement ? JSON.parse(editorConfigElement.textContent) : {};
        // Match rows by the inline's own markup: Django adds its dynamic-<prefix>
        // class during its own setup, which may run after this script.
        var rowSelector = '#' + prefix + '-group .inline-related:not(.empty-form)';

        function visibleRows() {
            return $(rowSelector).filter(function () {
                var deleteInput = $(this).find('input[name$="-DELETE"]');
                return !deleteInput.prop('checked') && $(this).is(':visible');
            });
        }

        function visibleSimulationRows() {
            return $(simulationRowSelector).filter(function () {
                var deleteInput = $(this).find('input[name$="-DELETE"]');
                return !deleteInput.prop('checked') && $(this).is(':visible');
            });
        }

        function updateOrders() {
            var rows = visibleRows().add(visibleSimulationRows()).get();
            rows.sort(function (left, right) {
                var leftOrder = parseInt($(left).find('input[name$="-display_order"]').val(), 10) || 0;
                var rightOrder = parseInt($(right).find('input[name$="-display_order"]').val(), 10) || 0;
                return leftOrder - rightOrder;
            });
            rows.forEach(function (row, index) {
                $(row).find('input[name$="-display_order"]').val(index);
            });
            validateStackOrders();
        }

        function activeLayerRows() {
            return visibleRows().filter(function () {
                return Boolean(
                    $(this).find('select[name$="-layer"]').val() ||
                    $(this).find('input[name$="-id"]').val()
                );
            });
        }

        function activeSimulationRows() {
            return visibleSimulationRows().filter(function () {
                return Boolean(
                    $(this).find('select[name$="-run"]').val() ||
                    $(this).find('input[name$="-id"]').val()
                );
            });
        }

        function setStackOrderError(input, message) {
            input.setCustomValidity(message);
            input.setAttribute('aria-invalid', message ? 'true' : 'false');
            var $message = $(input).siblings('.stack-order-error');
            if (message) {
                if (!$message.length) {
                    $message = $('<p class="errornote stack-order-error"></p>').insertAfter(input);
                }
                $message.text(message);
            } else {
                $message.remove();
            }
        }

        function validateStackOrders() {
            var layerInputs = activeLayerRows().find('input[name$="-display_order"]').get();
            var simulationInputs = activeSimulationRows()
                .find('input[name$="-display_order"]')
                .get();
            var inputs = layerInputs.concat(simulationInputs);
            inputs.forEach(function (input) {
                setStackOrderError(input, '');
            });
            simulationInputs.forEach(function (simulationInput) {
                var conflicts = layerInputs.filter(function (layerInput) {
                    return String(layerInput.value) === String(simulationInput.value);
                });
                if (!conflicts.length) return;
                var message = 'Stack order ' + simulationInput.value +
                    ' is already used by a catalog layer.';
                setStackOrderError(simulationInput, message);
                conflicts.forEach(function (layerInput) {
                    setStackOrderError(layerInput, message);
                });
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
            // Cache the server-rendered options before emptying the select.
            var options = assignmentOptions($select);
            $select.empty();
            options.each(function () {
                var optionLayerId = String($(this).attr('data-layer-id') || '');
                if (!optionLayerId || optionLayerId === layerId) {
                    $select.append($(this).clone());
                }
            });
            if (currentValue && $select.find('option[value="' + currentValue + '"]').length) {
                $select.val(currentValue);
            } else if (layerId) {
                $select.val($select.find('option[data-role="default"]').first().val() || '');
            } else {
                $select.val('');
            }
        }

        function serverLayerId($row) {
            // The layer the server rendered as selected, independent of when
            // this script runs relative to Django's own inline/autocomplete setup.
            var option = $row.find('select[name$="-layer"] option').filter(function () {
                return this.defaultSelected;
            }).first();
            return String(option.val() || '');
        }

        function initializeRow($row, chooseDefault) {
            var layerId = String($row.find('select[name$="-layer"]').val() || '');
            var previousLayerId = $row.data('initialized-layer-id');
            if (previousLayerId === undefined) previousLayerId = serverLayerId($row);
            // Autocomplete fires both change and select2:select; only a genuine
            // switch to another layer may reset the style to the layer default.
            filterAssignments($row, chooseDefault && layerId !== String(previousLayerId));
            $row.data('initialized-layer-id', layerId);
        }

        $(rowSelector).each(function () {
            initializeRow($(this), false);
        });

        $(document).on('change', rowSelector + ' select[name$="-layer"]', function () {
            initializeRow($(this).closest(rowSelector), true);
        });

        $(document).on('select2:select', rowSelector + ' select[name$="-layer"]', function () {
            var $row = $(this).closest(rowSelector);
            window.setTimeout(function () {
                initializeRow($row, true);
            }, 0);
        });

        // Django dispatches these as native CustomEvents carrying
        // detail.formsetName; the event target is the added row.
        function formsetName(event) {
            var detail = event.originalEvent && event.originalEvent.detail;
            return detail && detail.formsetName;
        }

        $(document).on('formset:added', function (event) {
            if (formsetName(event) === 'simulation') {
                var $simulationRow = $(event.target);
                $simulationRow.find('input[name$="-display_order"]').val(
                    visibleRows().add(visibleSimulationRows()).length - 1
                );
                loadSimulationOutputs($simulationRow);
                updateOrders();
                return;
            }
            if (formsetName(event) !== prefix) return;
            var $row = $(event.target);
            initializeRow($row, true);
            $row.find('input[name$="-display_order"]').val(visibleRows().length - 1);
            updateOrders();
        });

        $(document).on('formset:removed', function (event) {
            if ([prefix, 'simulation'].indexOf(formsetName(event)) !== -1) updateOrders();
        });

        $(document).on(
            'change',
            rowSelector + ' input[name$="-DELETE"], ' +
                simulationRowSelector + ' input[name$="-DELETE"]',
            updateOrders
        );

        $(document).on(
            'input change',
            rowSelector + ' input[name$="-display_order"], ' +
                simulationRowSelector + ' input[name$="-display_order"], ' +
                rowSelector + ' select[name$="-layer"], ' +
                simulationRowSelector + ' select[name$="-run"]',
            validateStackOrders
        );

        function simulationOutputContainer($row) {
            return $row.find('.field-map_outputs div[id$="-map_outputs"]').first();
        }

        function simulationOutputName($row) {
            return String($row.find('select[name$="-run"]').attr('name') || '')
                .replace(/-run$/, '-map_outputs');
        }

        function renderSimulationOutputs($row, outputs, message) {
            var $container = simulationOutputContainer($row);
            if (!$container.length) return;
            $container.empty();
            if (message) {
                $('<p class="help"></p>').text(message).appendTo($container);
                return;
            }
            var name = simulationOutputName($row);
            outputs.forEach(function (output, index) {
                var id = 'id_' + name + '_' + index;
                var $input = $('<input>', {
                    type: 'checkbox',
                    name: name,
                    value: output.id,
                    id: id,
                });
                $('<div></div>').append(
                    $('<label></label>', { for: id }).append(
                        $input,
                        document.createTextNode(' ' + output.label)
                    )
                ).appendTo($container);
            });
        }

        function loadSimulationOutputs($row) {
            var runId = String($row.find('select[name$="-run"]').val() || '');
            if (!runId) {
                renderSimulationOutputs($row, [], 'Select a run to see its map outputs.');
                return;
            }
            if (!editorConfig.simulationOutputsUrl) return;
            renderSimulationOutputs($row, [], 'Loading outputs…');
            var url = new URL(editorConfig.simulationOutputsUrl, window.location.origin);
            url.searchParams.set('run', runId);
            window.fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (response) {
                    if (!response.ok) throw new Error('Unable to load simulation outputs.');
                    return response.json();
                })
                .then(function (data) {
                    renderSimulationOutputs(
                        $row,
                        data.outputs,
                        data.outputs.length ? '' : 'This run has no map outputs.'
                    );
                })
                .catch(function () {
                    renderSimulationOutputs($row, [], 'Could not load this run\'s map outputs.');
                });
        }

        $(simulationRowSelector).each(function () {
            var $row = $(this);
            var hasRun = Boolean($row.find('select[name$="-run"]').val());
            var hasOptions = Boolean($row.find('input[name$="-map_outputs"]').length);
            if (!hasRun || !hasOptions) loadSimulationOutputs($row);
        });

        $(document).on(
            'change select2:select select2:clear',
            simulationRowSelector + ' select[name$="-run"]',
            function () {
                loadSimulationOutputs($(this).closest(simulationRowSelector));
            }
        );
        validateStackOrders();
    })(django.jQuery);
});
