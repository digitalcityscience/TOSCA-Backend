/*
 * Auto-increment/decrement display_order for GeoFeedback layers in Admin.
 * Uses Django's 'formset:added' and 'formset:removed' events.
 */

window.addEventListener('load', function() {
    (function($) {
        if (!$) {
            console.warn('GeoFeedback Admin: django.jQuery not found.');
            return;
        }

        function updateOrders(prefix) {
            // Select all visible rows for this formset
            var rows = $('.dynamic-' + prefix + ':visible');
            
            // Re-index them sequentially starting from 0
            rows.each(function(index) {
                var input = $(this).find('input[name$="-display_order"]');
                input.val(index);
            });
        }

        $(document).on('formset:added', function(event, $row, formsetName) {
            // Fallback for arguments
            var row = $row || $(event.target);
            var prefix = formsetName;
            
            if (!prefix) {
                var id = row.attr('id');
                if (id && id.indexOf('-') > -1) {
                    prefix = id.split('-')[0];
                }
            }

            if (prefix === 'feedbacklayer_set') {
                updateOrders(prefix);
            }
        });

        $(document).on('formset:removed', function(event, $row, formsetName) {
            var prefix = formsetName || 'feedbacklayer_set';
            
            if (prefix === 'feedbacklayer_set') {
                 updateOrders(prefix);
            }
        });

    })(django.jQuery);
});
