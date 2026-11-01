// SPDX-License-Identifier: LGPL-2.1-or-later
// Shows cached glTF models (three.js, vendored under /static/vendor/three). Each .viewer element has data-glb and,
// optionally, data-overlay (a glTF whose node named data-kind, "added" or "removed", is drawn translucent in
// data-color, the colours F3's change-set overlay uses). The camera frames the model's bounding box from a fixed
// direction, nothing animates, and body[data-rendered] is set to "1" once every viewer has drawn its frame (or
// "error" with the reason in body[data-render-error]), which is what the screenshot tests wait for.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const SIZE = { w: 480, h: 360 };
const loader = new GLTFLoader();

function load(url) {
  return new Promise((resolve, reject) => loader.load(url, resolve, undefined, reject));
}

function eachMesh(root, fn) {
  root.traverse((o) => { if (o.isMesh) fn(o); });
}

function node(gltf, name) {
  let found = null;
  gltf.scene.traverse((o) => { if (!found && o.name === name) found = o; });
  return found;
}

async function build(el) {
  const renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(SIZE.w, SIZE.h);
  renderer.setClearColor(0xe9edf1, 1);
  el.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a949c, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(1, 2, 3);
  scene.add(sun);

  const model = await load(el.dataset.glb);
  const grey = new THREE.MeshStandardMaterial({ color: 0xaeb4ba, roughness: 0.65, metalness: 0.1, side: THREE.DoubleSide });
  eachMesh(model.scene, (m) => { m.material = grey; });
  scene.add(model.scene);

  if (el.dataset.overlay) {
    const ov = await load(el.dataset.overlay);
    const part = node(ov, el.dataset.kind);
    if (part) {
      const mat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(el.dataset.color), transparent: true, opacity: parseFloat(el.dataset.opacity),
        side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
      });
      eachMesh(part, (m) => { m.material = mat; });
      scene.add(part);
    }
  }

  return { el, renderer, scene, box: new THREE.Box3().setFromObject(scene) };
}

// Viewers that share data-group are framed on the union of their bounding boxes, so side-by-side views have one scale.
function frame(v, box) {
  const centre = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-6);
  const camera = new THREE.PerspectiveCamera(35, SIZE.w / SIZE.h, radius / 100, radius * 100);
  const dir = new THREE.Vector3(1, 0.8, 1).normalize();
  camera.position.copy(centre).addScaledVector(dir, radius / Math.sin((35 * Math.PI) / 360) * 1.05);
  camera.lookAt(centre);
  const controls = new OrbitControls(camera, v.renderer.domElement);
  controls.target.copy(centre);
  controls.enableDamping = false;
  controls.update();
  const draw = () => v.renderer.render(v.scene, camera);
  controls.addEventListener("change", draw);
  draw();
}

const els = [...document.querySelectorAll(".viewer")];
Promise.all(els.map(build)).then(
  (views) => {
    const groups = {};
    for (const v of views) {
      const g = v.el.dataset.group || v.el.id;
      groups[g] = (groups[g] || new THREE.Box3()).union(v.box);
    }
    for (const v of views) frame(v, groups[v.el.dataset.group || v.el.id]);
    document.body.dataset.rendered = "1";
  },
  (err) => { document.body.dataset.renderError = String(err && err.message || err); document.body.dataset.rendered = "error"; },
);
