/*
 * Editor.js "Map scene" block tool for GeoStory content.
 *
 * Saves {scene_id} only. The admin passes the story's saved scenes through the
 * textarea's data-editorjs-map-scenes attribute; init.js registers this tool
 * when that attribute is present. Readers switch the map to the scene when the
 * anchor scrolls past the middle of the viewport.
 */
(function () {
    "use strict";

    var ICON =
        '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"></polygon>' +
        '<line x1="8" y1="2" x2="8" y2="18"></line><line x1="16" y1="6" x2="16" y2="22"></line></svg>';

    function MapSceneTool(options) {
        this.data = options.data || {};
        this.scenes = (options.config && options.config.scenes) || [];
        this.readOnly = options.readOnly;
        this.select = null;
    }

    MapSceneTool.toolbox = { title: "Map scene", icon: ICON };
    MapSceneTool.isReadOnlySupported = true;

    MapSceneTool.prototype.render = function () {
        var wrapper = document.createElement("div");
        wrapper.className = "geostory-map-scene-block";

        var label = document.createElement("label");
        label.className = "geostory-map-scene-block__label";
        label.innerHTML = ICON;
        var text = document.createElement("span");
        text.textContent = "Map switches to scene";
        label.appendChild(text);
        wrapper.appendChild(label);

        if (!this.scenes.length) {
            var empty = document.createElement("p");
            empty.className = "geostory-map-scene-block__empty";
            empty.textContent = "This story has no saved scenes yet. Add a scene, then pick it here.";
            wrapper.appendChild(empty);
            return wrapper;
        }

        var select = document.createElement("select");
        select.className = "geostory-map-scene-block__select";
        select.disabled = !!this.readOnly;
        var placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Choose a scene…";
        select.appendChild(placeholder);
        var known = false;
        this.scenes.forEach(function (scene) {
            var option = document.createElement("option");
            option.value = scene.id;
            option.textContent = (scene.order + 1) + ". " + scene.title;
            select.appendChild(option);
            if (scene.id === this.data.scene_id) known = true;
        }, this);
        if (this.data.scene_id && !known) {
            // Keep a dangling anchor visible so the author can fix it; saving
            // the story reports it as an error.
            var missing = document.createElement("option");
            missing.value = this.data.scene_id;
            missing.textContent = "Missing scene (deleted?)";
            select.appendChild(missing);
        }
        select.value = this.data.scene_id || "";
        label.htmlFor = select.id = "map-scene-" + Math.random().toString(36).slice(2);
        wrapper.appendChild(select);

        var link = document.createElement("a");
        link.className = "geostory-map-scene-block__link";
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = "Open scene";
        var scenes = this.scenes;
        function updateLink() {
            var scene = scenes.find(function (item) { return item.id === select.value; });
            link.hidden = !scene;
            if (scene) link.href = scene.url;
        }
        select.addEventListener("change", updateLink);
        updateLink();
        wrapper.appendChild(link);

        this.select = select;
        return wrapper;
    };

    MapSceneTool.prototype.save = function () {
        return { scene_id: this.select ? this.select.value : (this.data.scene_id || "") };
    };

    MapSceneTool.prototype.validate = function (savedData) {
        // Editor.js drops blocks that fail validation, so an unpicked anchor
        // never reaches the server.
        return Boolean(savedData.scene_id);
    };

    window.MapSceneTool = MapSceneTool;
})();
