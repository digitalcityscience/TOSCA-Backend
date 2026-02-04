/**
 * Simple Secure Fields Toggle - Individual field toggles like password field
 */
(function($) {
    'use strict';
    
    $(document).ready(function() {
        console.log('Simple secure fields initializing...');
        initSecureFields();
        addToggleButtons();
    });
    
    function initSecureFields() {
        // Mask sensitive text fields on load
        $('.secure-field-text').each(function() {
            const $field = $(this);
            const originalValue = $field.val();
            
            if (originalValue && originalValue.length > 0) {
                $field.data('original-value', originalValue);
                $field.val(maskValue(originalValue));
                $field.attr('readonly', true);
                $field.addClass('masked');
            }
        });
        
        // Handle number fields
        $('.secure-field-number').each(function() {
            const $field = $(this);
            const originalValue = $field.val();
            
            if (originalValue && originalValue.length > 0) {
                $field.data('original-value', originalValue);
                $field.val('••••');
                $field.attr('readonly', true);
                $field.addClass('masked');
            }
        });
    }
    
    function addToggleButtons() {
        // Add toggle buttons next to sensitive fields
        $('.secure-field-text, .secure-field-number').each(function() {
            const $field = $(this);
            const fieldName = $field.attr('name');
            
            if (!$field.next('.field-toggle').length) {
                const $toggle = $('<button type="button" class="field-toggle" data-field="' + fieldName + '">👁️</button>');
                $field.after($toggle);
                
                $toggle.on('click', function(e) {
                    e.preventDefault();
                    toggleField($field, $(this));
                });
            }
        });
        
        // Handle password field separately
        $('.secure-field-password').each(function() {
            const $field = $(this);
            
            if (!$field.next('.field-toggle').length) {
                const $toggle = $('<button type="button" class="field-toggle" data-field="password">👁️</button>');
                $field.after($toggle);
                
                $toggle.on('click', function(e) {
                    e.preventDefault();
                    togglePasswordField($field, $(this));
                });
            }
        });
    }
    
    function toggleField($field, $button) {
        const isHidden = $field.hasClass('masked');
        
        if (isHidden) {
            // Show field
            const originalValue = $field.data('original-value');
            if (originalValue) {
                $field.val(originalValue);
            }
            $field.attr('readonly', false);
            $field.removeClass('masked');
            $button.text('🙈');
            $button.attr('title', 'Hide value');
        } else {
            // Hide field
            const currentValue = $field.val();
            if (currentValue) {
                $field.data('original-value', currentValue);
                if ($field.hasClass('secure-field-number')) {
                    $field.val('••••');
                } else {
                    $field.val(maskValue(currentValue));
                }
            }
            $field.attr('readonly', true);
            $field.addClass('masked');
            $button.text('👁️');
            $button.attr('title', 'Show value');
        }
    }
    
    function togglePasswordField($field, $button) {
        const isPassword = $field.attr('type') === 'password';
        
        if (isPassword) {
            // Show password
            $field.attr('type', 'text');
            $button.text('🙈');
            $button.attr('title', 'Hide password');
        } else {
            // Hide password
            $field.attr('type', 'password');
            $button.text('👁️');
            $button.attr('title', 'Show password');
        }
    }
    
    function maskValue(value) {
        if (!value || value.length === 0) return value;
        if (value.includes('•')) return value; // Already masked
        
        if (value.length <= 4) {
            return '•'.repeat(value.length);
        }
        
        return value.substring(0, 2) + '•'.repeat(value.length - 4) + value.substring(value.length - 2);
    }
    
})(django.jQuery);