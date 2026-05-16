/**
 * timers-global.js — Touch UI timer utilities stub.
 * Provides the TouchTimers namespace so touch-menu.js stops emitting a 404.
 */
(function () {
    if (window.TouchTimers && window.TouchTimers._installed) return;
    window.TouchTimers = {
        _installed: true,
        _timers: {},
        set: function (id, fn, delay) {
            this.clear(id);
            this._timers[id] = setTimeout(fn, delay);
        },
        clear: function (id) {
            if (this._timers[id]) {
                clearTimeout(this._timers[id]);
                delete this._timers[id];
            }
        },
        clearAll: function () {
            Object.keys(this._timers).forEach(id => this.clear(id));
        },
    };
})();
