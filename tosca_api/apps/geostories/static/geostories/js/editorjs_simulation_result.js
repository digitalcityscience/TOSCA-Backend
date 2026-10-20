/* Editor.js block that places a saved, validated simulation result presentation. */
(function () {
    "use strict";

    var ICON =
        '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M3 3v18h18"></path><path d="m7 16 4-5 4 3 5-7"></path></svg>';

    function SimulationResultTool(options) {
        this.data = options.data || {};
        this.presentations = (options.config && options.config.presentations) || [];
        this.readOnly = options.readOnly;
        this.select = null;
    }

    SimulationResultTool.toolbox = { title: "Simulation result", icon: ICON };
    SimulationResultTool.isReadOnlySupported = true;

    SimulationResultTool.prototype.render = function () {
        var wrapper = document.createElement("div");
        wrapper.className = "geostory-simulation-result-block";

        var label = document.createElement("label");
        label.className = "geostory-simulation-result-block__label";
        label.innerHTML = ICON;
        var text = document.createElement("span");
        text.textContent = "Simulation result";
        label.appendChild(text);
        wrapper.appendChild(label);

        if (!this.presentations.length) {
            var empty = document.createElement("p");
            empty.className = "geostory-simulation-result-block__empty";
            empty.textContent = "Add a simulation result to this story first, then select it here.";
            wrapper.appendChild(empty);
            return wrapper;
        }

        var select = document.createElement("select");
        select.className = "geostory-simulation-result-block__select";
        select.disabled = !!this.readOnly;
        var placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Choose a result…";
        select.appendChild(placeholder);
        var known = false;
        this.presentations.forEach(function (presentation) {
            var option = document.createElement("option");
            option.value = presentation.id;
            option.textContent = presentation.caption + " (" + presentation.view + ")";
            select.appendChild(option);
            if (presentation.id === this.data.presentation_id) known = true;
        }, this);
        if (this.data.presentation_id && !known) {
            var missing = document.createElement("option");
            missing.value = this.data.presentation_id;
            missing.textContent = "Missing result (deleted?)";
            select.appendChild(missing);
        }
        select.value = this.data.presentation_id || "";
        label.htmlFor = select.id = "simulation-result-" + Math.random().toString(36).slice(2);
        wrapper.appendChild(select);
        this.select = select;
        return wrapper;
    };

    SimulationResultTool.prototype.save = function () {
        return {
            presentation_id: this.select
                ? this.select.value
                : (this.data.presentation_id || ""),
        };
    };

    SimulationResultTool.prototype.validate = function (savedData) {
        return Boolean(savedData.presentation_id);
    };

    window.SimulationResultTool = SimulationResultTool;
})();
