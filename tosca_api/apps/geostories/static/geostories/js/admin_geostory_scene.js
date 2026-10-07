/* Scene layer inline: keep style options scoped to the chosen layer and
 * display_order sequential (0 = bottom). */
window.addEventListener('load', function () {
    (function ($) {
        if (!$) return;

        var prefix = 'scene_layers';

        function visibleRows() {
            return $('.dynamic-' + prefix).filter(function () {
                var deleteInput = $(this).find('input[name$="-DELETE"]');
                return !deleteInput.prop('checked') && $(this).is(':visible');
            });
        }

        function updateOrders() {
            var rows = visibleRows().get();
            rows.sort(function (left, right) {
                var leftOrder = parseInt($(left).find('input[name$="-display_order"]').val(), 10) || 0;
                var rightOrder = parseInt($(right).find('input[name$="-display_order"]').val(), 10) || 0;
                return leftOrder - rightOrder;
            });
            rows.forEach(function (row, index) {
                $(row).find('input[name$="-display_order"]').val(index);
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
            $select.empty();
            assignmentOptions($select).each(function () {
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

        function initializeRow($row, chooseDefault) {
            var layerId = String($row.find('select[name$="-layer"]').val() || '');
            var previousLayerId = String($row.data('initialized-layer-id') || '');
            // Autocomplete fires both change and select2:select; only the first
            // event for a new layer may reset the style to the layer default.
            filterAssignments($row, chooseDefault && layerId !== previousLayerId);
            $row.data('initialized-layer-id', layerId);
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
            $row = $row || $(event.target);
            initializeRow($row, true);
            $row.find('input[name$="-display_order"]').val(visibleRows().length - 1);
            updateOrders();
        });

        $(document).on('formset:removed', function (event, $row, formsetName) {
            if (formsetName === prefix) updateOrders();
        });

        $(document).on('change', '.dynamic-' + prefix + ' input[name$="-DELETE"]', updateOrders);
    })(django.jQuery);
});
