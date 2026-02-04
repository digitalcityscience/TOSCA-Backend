/**
 * Secure Fields Toggle - Show/Hide sensitive PostGIS connection details
 */
(function($) {
    'use strict';
    
    $(document).ready(function() {
        // Wait for DOM to be fully loaded
        setTimeout(function() {
            initSecureFields();
            addToggleButtons();
        }, 100);
    });
    
    function initSecureFields() {
        console.log('Initializing secure fields...');
        
        // Hide all secure fields by default except password (already hidden)
        $('.secure-field').not('[type="password"]').each(function() {
            const $field = $(this);
            const originalValue = $field.val();
            
            console.log('Processing field:', $field.attr('name'), 'value:', originalValue);
            
            if (originalValue && originalValue.length > 0) {
                // Store original value and mask it
                $field.data('original-value', originalValue);
                const maskedValue = maskValue(originalValue);
                $field.val(maskedValue);
                $field.prop('readonly', true);
                $field.addClass('masked-field');
                console.log('Masked field:', $field.attr('name'), 'from', originalValue, 'to', maskedValue);
            }
        });
    }
    
    function addToggleButtons() {
        // Find the PostGIS Connection fieldset
        const $fieldset = $('.secure-fieldset');
        console.log('Found secure fieldsets:', $fieldset.length);
        
        if ($fieldset.length === 0) return;
        
        // Add toggle button to fieldset header
        const $legend = $fieldset.find('h2').first();
        console.log('Found legend:', $legend.length);
        
        if ($legend.length > 0) {
            // Remove existing toggle if any
            $legend.find('.secure-toggle-container').remove();
            
            const toggleHtml = `
                <span class="secure-toggle-container">
                    <button type="button" class="btn-toggle-secure" data-visible="false">
                        👁️ Show Details
                    </button>
                </span>
            `;
            $legend.append(toggleHtml);
            
            console.log('Toggle button added');
            
            // Bind click event
            $('.btn-toggle-secure').off('click').on('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                toggleSecureFields.call(this);
            });
        }
    }
    
    function toggleSecureFields() {
        const $button = $(this);
        const isVisible = $button.data('visible') === true || $button.data('visible') === 'true';
        
        console.log('Toggling fields, current state:', isVisible);
        
        if (isVisible) {
            // Hide fields
            hideSecureFields();
            $button.html('👁️ Show Details');
            $button.data('visible', false);
        } else {
            // Show fields
            showSecureFields();
            $button.html('🙈 Hide Details');
            $button.data('visible', true);
        }
    }
    
    function showSecureFields() {
        console.log('Showing secure fields...');
        
        // Show actual values for text fields
        $('.secure-field').not('[type="password"]').each(function() {
            const $field = $(this);
            const originalValue = $field.data('original-value');
            
            if (originalValue) {
                $field.val(originalValue);
                $field.prop('readonly', false);
                $field.removeClass('masked-field');
                console.log('Showing field:', $field.attr('name'), 'value:', originalValue);
            }
        });
        
        // Show password field value
        $('[type="password"].secure-field').each(function() {
            const $field = $(this);
            $field.attr('type', 'text');
            $field.prop('readonly', false);
        });
    }
    
    function hideSecureFields() {
        console.log('Hiding secure fields...');
        
        // Mask text fields
        $('.secure-field').not('[type="text"],[type="password"]').each(function() {
            const $field = $(this);
            let currentValue = $field.val();
            
            // Save current value if it's different from stored original
            if (currentValue && currentValue.length > 0) {
                $field.data('original-value', currentValue);
                const maskedValue = maskValue(currentValue);
                $field.val(maskedValue);
                $field.prop('readonly', true);
                $field.addClass('masked-field');
                console.log('Hiding field:', $field.attr('name'), 'masked to:', maskedValue);
            }
        });
        
        // Also handle text inputs that were shown
        $('[type="text"].secure-field').each(function() {
            const $field = $(this);
            let currentValue = $field.val();
            
            // Only mask if it doesn't already look masked
            if (currentValue && !currentValue.includes('•')) {
                $field.data('original-value', currentValue);
                const maskedValue = maskValue(currentValue);
                $field.val(maskedValue);
                $field.prop('readonly', true);
                $field.addClass('masked-field');
            }
        });
        
        // Hide password field value (convert back to password type)
        $('[type="text"].secure-field').filter(function() {
            return $(this).attr('name') && $(this).attr('name').toLowerCase().includes('password');
        }).each(function() {
            $(this).attr('type', 'password');
            $(this).prop('readonly', false); // Allow editing password
        });
    }
    
    function maskValue(value) {
        if (!value || value.length === 0) return value;
        if (value.includes('•')) return value; // Already masked
        
        if (value.length <= 4) {
            return '•'.repeat(value.length);
        }
        
        // Show first 2 and last 2 characters
        return value.substring(0, 2) + '•'.repeat(value.length - 4) + value.substring(value.length - 2);
    }
    
})(django.jQuery);