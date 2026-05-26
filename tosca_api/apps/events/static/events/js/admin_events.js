window.addEventListener("load", function () {
    (function ($) {
        if (!$) {
            console.warn("Events Admin: django.jQuery not found.");
            return;
        }

        function fieldRow(fieldName) {
            return $(".form-row.field-" + fieldName);
        }

        function inlineGroupFor(fieldNameSuffix) {
            return $(".inline-group").filter(function () {
                return $(this).find("[name$='-" + fieldNameSuffix + "']").length > 0;
            });
        }

        function selectedEventTypeOption() {
            return $("#id_event_type option:selected");
        }

        function updateTaxonomyFields(profileKey) {
            $("[data-taxonomy-profile-key]").each(function () {
                var fieldProfileKey = $(this).data("taxonomy-profile-key") || "";
                var shouldShow = !fieldProfileKey || fieldProfileKey === profileKey;
                var row = $(this).closest(".form-row");
                row.toggle(shouldShow);
            });

            $(".events-taxonomy-section").each(function () {
                var section = $(this);
                var visibleRows = section.find(".form-row:visible").length;
                section.toggle(visibleRows > 0);
            });
        }

        function updateSeriesForm() {
            var seriesMode = $("#id_series_mode").val();
            var recurrenceType = $("#id_recurrence_type").val();
            var monthlyRuleType = $("#id_monthly_rule_type").val();
            var isRecurring = seriesMode === "recurring";
            var isManualBatch = seriesMode === "manual_batch";

            [
                "recurrence_type",
                "end_date",
                "occurrence_count",
                "interval"
            ].forEach(function (fieldName) {
                fieldRow(fieldName).toggle(isRecurring);
            });

            fieldRow("by_weekday").toggle(isRecurring && recurrenceType === "weekly");
            fieldRow("monthly_rule_type").toggle(isRecurring && recurrenceType === "monthly");

            var showDayOfMonth =
                isRecurring &&
                recurrenceType === "monthly" &&
                monthlyRuleType === "day_of_month";
            var showNthWeekday =
                isRecurring &&
                recurrenceType === "monthly" &&
                monthlyRuleType === "nth_weekday";

            fieldRow("day_of_month").toggle(showDayOfMonth);
            fieldRow("week_of_month").toggle(showNthWeekday);
            fieldRow("weekday_of_month").toggle(showNthWeekday);

            inlineGroupFor("occurrence_date").toggle(isManualBatch);
        }

        function updateEventForm() {
            var locationMode = $("#id_location_mode").val();
            var selectedOption = selectedEventTypeOption();
            var profileMode = selectedOption.data("profile-mode");
            var profileKey = selectedOption.data("profile-key");
            var showOnlineFields = locationMode === "online" || locationMode === "hybrid";
            var showLocationField = locationMode === "physical" || locationMode === "hybrid";

            fieldRow("location").toggle(showLocationField);
            fieldRow("online_url").toggle(showOnlineFields);
            fieldRow("online_platform").toggle(showOnlineFields);

            $(".events-profile-section").toggle(false);

            if (profileMode === "extension" && profileKey) {
                $(".events-profile-" + profileKey).toggle(true);
            }

            updateTaxonomyFields(profileKey || "");
        }

        $("#id_series_mode, #id_recurrence_type, #id_monthly_rule_type").on(
            "change",
            updateSeriesForm
        );
        $("#id_location_mode, #id_event_type").on("change", updateEventForm);

        updateSeriesForm();
        updateEventForm();
    })(django.jQuery);
});
