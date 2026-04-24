/*
 * Auto-increment/decrement display_order for GeoStory layers in Admin.
 * Uses Django's 'formset:added' and 'formset:removed' events.
 */

window.addEventListener('load', function() {
    (function($) {
        if (!$) {
            console.warn('GeoStory Admin: django.jQuery not found.');
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

            if (prefix === 'geostorylayer_set') {
                updateOrders(prefix);
            }
        });

        $(document).on('formset:removed', function(event, $row, formsetName) {
            // Verify functionality by checking if the specific formset exists,
            // fallback to default 'geostorylayer_set' if prefix is missing.
            
            var prefix = formsetName || 'geostorylayer_set';
            
            if (prefix === 'geostorylayer_set') {
                 updateOrders(prefix);
            }
        });

    })(django.jQuery);
});
