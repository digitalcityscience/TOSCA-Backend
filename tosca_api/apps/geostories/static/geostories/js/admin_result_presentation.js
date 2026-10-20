(function () {
    "use strict";

    function init() {
        var output = document.getElementById("id_output");
        var view = document.getElementById("id_view_type");
        if (!output || !view) return;

        var summary = document.createElement("div");
        summary.className = "result-output-summary";
        output.parentNode.appendChild(summary);

        function fieldRow(name) {
            var field = document.getElementById("id_" + name);
            return field && (field.closest(".form-row") || field.closest("div.fieldBox"));
        }

        function showFields() {
            var groups = {
                table: ["table_columns", "sort_field", "sort_direction"],
                metric_grid: ["metrics"],
                bar_chart: ["category_field", "series_fields"],
                line_chart: ["x_field", "series_fields"],
            };
            var all = [
                "table_columns", "sort_field", "sort_direction", "metrics",
                "category_field", "x_field", "series_fields",
            ];
            all.forEach(function (name) {
                var row = fieldRow(name);
                if (row) row.hidden = !(groups[view.value] || []).includes(name);
            });
        }

        function replaceOptions(name, choices, emptyLabel) {
            var select = document.getElementById("id_" + name);
            if (!select) return;
            var selected = Array.from(select.selectedOptions).map(function (option) {
                return option.value;
            });
            select.replaceChildren();
            if (emptyLabel !== undefined) {
                var empty = document.createElement("option");
                empty.value = "";
                empty.textContent = emptyLabel;
                select.appendChild(empty);
            }
            choices.forEach(function (choice) {
                var option = document.createElement("option");
                option.value = choice.value;
                option.textContent = choice.label;
                option.selected = selected.includes(choice.value);
                select.appendChild(option);
            });
            if (name === "table_columns" && selected.length === 0) {
                Array.from(select.options).forEach(function (option) { option.selected = true; });
            }
        }

        function fieldLabel(field) {
            var unit = field.unit ? "; " + field.unit : "";
            return field.label + " (" + field.key + "; " + field.type + unit + ")";
        }

        function applySchema(schema) {
            var fields = schema.fields.map(function (field) {
                return { value: field.key, label: fieldLabel(field), type: field.type };
            });
            var numeric = fields.filter(function (field) {
                return field.type === "integer" || field.type === "number";
            });
            var axes = fields.filter(function (field) {
                return field.type === "datetime" || field.type === "integer" || field.type === "number";
            });
            replaceOptions("table_columns", fields);
            replaceOptions("sort_field", fields, "— none —");
            replaceOptions("category_field", fields, "— choose a category —");
            replaceOptions("x_field", axes, "— choose an axis —");
            replaceOptions("series_fields", numeric);
            replaceOptions("metrics", schema.metrics.map(function (metric) {
                return { value: metric, label: metric };
            }));

            var metricOption = view.querySelector('option[value="metric_grid"]');
            if (metricOption) metricOption.disabled = schema.profile !== "metric_set";
            if (schema.profile !== "metric_set" && view.value === "metric_grid") {
                view.value = "table";
                showFields();
            }
            summary.innerHTML = "";
            var strong = document.createElement("strong");
            strong.textContent = schema.source + ": " + schema.owner;
            summary.appendChild(strong);
            summary.appendChild(document.createTextNode(
                " · Output: " + schema.output + " · role: " + schema.role + " · profile: " + schema.profile
            ));
        }

        function loadSchema() {
            var baseUrl = output.dataset.resultSchemaUrl;
            if (!baseUrl || !output.value) {
                summary.textContent = "Choose an output to load its available fields.";
                return;
            }
            summary.textContent = "Loading output fields…";
            fetch(baseUrl + "?output=" + encodeURIComponent(output.value), {
                headers: { "X-Requested-With": "XMLHttpRequest" },
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("Could not load output schema");
                    return response.json();
                })
                .then(applySchema)
                .catch(function () {
                    summary.textContent = "The selected output's fields could not be loaded.";
                });
        }

        view.addEventListener("change", showFields);
        output.addEventListener("change", loadSchema);
        showFields();
        loadSchema();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
