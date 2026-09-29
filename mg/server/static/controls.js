/* Orbit camera controls for the mapgen viewport.
 *
 * Written from scratch (~130 lines) instead of shipping three's examples/js file, so the
 * static bundle stays small and there is nothing to fall out of sync with the vendored
 * three.js revision.
 *
 *   left drag   orbit        right / middle drag  pan
 *   wheel       dolly        shift + drag         pan
 */
(function (global) {
  "use strict";

  const THREE = global.THREE;

  class OrbitControls {
    constructor(camera, domElement) {
      this.camera = camera;
      this.domElement = domElement;
      this.target = new THREE.Vector3(0, 0, 0);
      this.minDistance = 5;
      this.maxDistance = 100000;
      this.minPolar = 0.02;
      this.maxPolar = Math.PI * 0.495;
      this.rotateSpeed = 1.0;
      this.zoomSpeed = 0.9;
      this.panSpeed = 1.0;
      this.damping = 0.12;
      this.enabled = true;
      this.autoRotate = false;
      this.autoRotateSpeed = 0.35;

      this._spherical = new THREE.Spherical();
      this._sphericalDelta = new THREE.Spherical(0, 0, 0);
      this._panOffset = new THREE.Vector3();
      this._scale = 1;
      this._state = "none";
      this._pointers = new Map();
      this._last = { x: 0, y: 0 };
      this._touchDist = 0;

      this._onDown = this._onDown.bind(this);
      this._onMove = this._onMove.bind(this);
      this._onUp = this._onUp.bind(this);
      this._onWheel = this._onWheel.bind(this);
      this._onContext = (e) => e.preventDefault();

      domElement.addEventListener("pointerdown", this._onDown);
      domElement.addEventListener("pointermove", this._onMove);
      domElement.addEventListener("pointerup", this._onUp);
      domElement.addEventListener("pointercancel", this._onUp);
      domElement.addEventListener("wheel", this._onWheel, { passive: false });
      domElement.addEventListener("contextmenu", this._onContext);
      this._offset = new THREE.Vector3().copy(camera.position).sub(this.target);
    }

    dispose() {
      const d = this.domElement;
      d.removeEventListener("pointerdown", this._onDown);
      d.removeEventListener("pointermove", this._onMove);
      d.removeEventListener("pointerup", this._onUp);
      d.removeEventListener("pointercancel", this._onUp);
      d.removeEventListener("wheel", this._onWheel);
      d.removeEventListener("contextmenu", this._onContext);
    }

    setTarget(v, keepCamera) {
      const delta = new THREE.Vector3().subVectors(v, this.target);
      this.target.copy(v);
      if (keepCamera) this.camera.position.add(delta);
    }

    /** Point the camera at a target from a spherical offset. */
    frame(target, radius, thetaDeg, phiDeg) {
      this.target.copy(target);
      const theta = (thetaDeg * Math.PI) / 180;
      const phi = (phiDeg * Math.PI) / 180;
      this.camera.position.set(
        target.x + radius * Math.sin(phi) * Math.sin(theta),
        target.y + radius * Math.cos(phi),
        target.z + radius * Math.sin(phi) * Math.cos(theta)
      );
      this.camera.lookAt(target);
      this._offset.copy(this.camera.position).sub(this.target);
      this.update();
    }

    _onDown(e) {
      if (!this.enabled) return;
      this.domElement.setPointerCapture(e.pointerId);
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this._pointers.size === 1) {
        this._state = e.button === 0 && !e.shiftKey ? "rotate" : "pan";
      } else if (this._pointers.size === 2) {
        this._state = "touch-zoom";
        this._touchDist = this._pointerDistance();
      }
      if (this._state === "rotate") this.autoRotate = false;
      this._last = { x: e.clientX, y: e.clientY };
      this._moved = false;
    }

    _onMove(e) {
      if (!this.enabled || !this._pointers.has(e.pointerId)) return;
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const dx = e.clientX - this._last.x;
      const dy = e.clientY - this._last.y;
      if (Math.abs(dx) + Math.abs(dy) > 1) this._moved = true;

      if (this._state === "rotate") {
        const el = this.domElement.clientHeight || 1;
        this._sphericalDelta.theta -= (2 * Math.PI * dx * this.rotateSpeed) / el;
        this._sphericalDelta.phi -= (2 * Math.PI * dy * this.rotateSpeed) / el;
      } else if (this._state === "pan") {
        const el = this.domElement.clientHeight || 1;
        const dist = this.camera.position.distanceTo(this.target);
        const fov = (this.camera.fov * Math.PI) / 180;
        const scale = (2 * dist * Math.tan(fov / 2)) / el;
        const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
        const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
        this._panOffset.addScaledVector(right, -dx * scale * this.panSpeed);
        this._panOffset.addScaledVector(up, dy * scale * this.panSpeed);
      } else if (this._state === "touch-zoom" && this._pointers.size === 2) {
        const d = this._pointerDistance();
        if (this._touchDist > 0) this._scale *= this._touchDist / d;
        this._touchDist = d;
      }
      this._last = { x: e.clientX, y: e.clientY };
      this.update();
    }

    _onUp(e) {
      if (this._pointers.has(e.pointerId)) this._pointers.delete(e.pointerId);
      if (this._pointers.size === 0) this._state = "none";
      this._touchDist = 0;
      try {
        this.domElement.releasePointerCapture(e.pointerId);
      } catch (err) {
        /* pointer already released */
      }
    }

    _onWheel(e) {
      if (!this.enabled) return;
      e.preventDefault();
      const amount = e.deltaY > 0 ? 1.08 : 1 / 1.08;
      this._scale *= amount;
      this.update();
    }

    _pointerDistance() {
      const pts = Array.from(this._pointers.values());
      if (pts.length < 2) return 0;
      return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    }

    update() {
      const offset = new THREE.Vector3().copy(this.camera.position).sub(this.target);
      this._spherical.setFromVector3(offset);
      if (this.autoRotate) this._spherical.theta += this.autoRotateSpeed * 0.01;
      this._spherical.theta += this._sphericalDelta.theta;
      this._spherical.phi += this._sphericalDelta.phi;
      this._spherical.phi = Math.max(this.minPolar, Math.min(this.maxPolar, this._spherical.phi));
      this._spherical.radius *= this._scale;
      this._spherical.radius = Math.max(this.minDistance,
        Math.min(this.maxDistance, this._spherical.radius));
      this.target.add(this._panOffset);
      offset.setFromSpherical(this._spherical);
      this.camera.position.copy(this.target).add(offset);
      this.camera.lookAt(this.target);
      const keep = this.damping;
      this._sphericalDelta.theta *= 1 - keep;
      this._sphericalDelta.phi *= 1 - keep;
      this._panOffset.multiplyScalar(1 - keep);
      this._scale = 1 + (this._scale - 1) * (1 - keep);
    }

    /** True when the last pointer interaction was a click, not a drag. */
    wasClick() {
      return !this._moved;
    }
  }

  global.OrbitControls = OrbitControls;
})(window);
