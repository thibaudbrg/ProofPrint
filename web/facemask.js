// Option C - live in-browser face mask (the demo "deepfake").
//
// MediaPipe FaceLandmarker finds 478 points on the live camera face; the same
// topology on a still source face lets us warp the source onto the live face
// triangle-by-triangle (Delaunay). The result is drawn to a canvas and handed
// back as a MediaStream, so it flows through the normal selfie pipeline. No OS
// virtual camera is needed - we own the page - so this works on the phone.
//
// It's a face-mesh overlay (Snapchat-lens grade), not an offline swap. It warps
// hard at a full profile and at fast motion; for the demo that IS the point
// (Act 2's profile-turn catch). Depends on the global `Delaunator` (loaded
// before this module) and reads /web/attack/source_face.jpg.
import { FaceLandmarker, FilesetResolver } from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";

const VISION = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const MODEL  = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";
const SRC_URL = "/web/attack/source_face.jpg";

let vidLm = null, imgLm = null, srcImg = null, srcPts = null, tris = null, ready = null;
let cam = null, video = null, canvas = null, ctx = null, raf = 0, lastTs = 0;

function loadImg(url){
  return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url; });
}

// One-time: load the model, landmark the source face, triangulate it.
async function ensureReady(){
  if (ready) return ready;
  ready = (async () => {
    const fileset = await FilesetResolver.forVisionTasks(VISION);
    const base = { baseOptions: { modelAssetPath: MODEL }, numFaces: 1, outputFaceBlendshapes: false, outputFacialTransformationMatrixes: false };
    vidLm = await FaceLandmarker.createFromOptions(fileset, { ...base, runningMode: "VIDEO" });
    imgLm = await FaceLandmarker.createFromOptions(fileset, { ...base, runningMode: "IMAGE" });
    srcImg = await loadImg(SRC_URL);
    const r = imgLm.detect(srcImg);
    if (!r.faceLandmarks || !r.faceLandmarks.length) throw new Error("no face found in source_face.jpg");
    const lm = r.faceLandmarks[0];
    srcPts = lm.map(p => [p.x * srcImg.naturalWidth, p.y * srcImg.naturalHeight]);
    const coords = new Float64Array(srcPts.length * 2);
    srcPts.forEach((p, i) => { coords[2*i] = p[0]; coords[2*i+1] = p[1]; });
    tris = new Delaunator(coords).triangles;   // index triples, reused for the live face
  })();
  return ready;
}

// Warp one source triangle onto its destination triangle (affine + clip).
function warpTri(s0, s1, s2, d0, d1, d2){
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(d0[0], d0[1]); ctx.lineTo(d1[0], d1[1]); ctx.lineTo(d2[0], d2[1]); ctx.closePath();
  ctx.clip();
  const [x0,y0]=s0,[x1,y1]=s1,[x2,y2]=s2,[u0,v0]=d0,[u1,v1]=d1,[u2,v2]=d2;
  const den = x0*(y1-y2) + x1*(y2-y0) + x2*(y0-y1);
  if (Math.abs(den) > 1e-6){
    const a = (u0*(y1-y2) + u1*(y2-y0) + u2*(y0-y1)) / den;
    const b = (v0*(y1-y2) + v1*(y2-y0) + v2*(y0-y1)) / den;
    const c = (u0*(x2-x1) + u1*(x0-x2) + u2*(x1-x0)) / den;
    const d = (v0*(x2-x1) + v1*(x0-x2) + v2*(x1-x0)) / den;
    const e = (u0*(x1*y2 - x2*y1) + u1*(x2*y0 - x0*y2) + u2*(x0*y1 - x1*y0)) / den;
    const f = (v0*(x1*y2 - x2*y1) + v1*(x2*y0 - x0*y2) + v2*(x0*y1 - x1*y0)) / den;
    ctx.setTransform(a, b, c, d, e, f);
    ctx.drawImage(srcImg, 0, 0);
  }
  ctx.restore();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

function render(){
  raf = requestAnimationFrame(render);
  if (!video || video.readyState < 2) return;
  const w = canvas.width, h = canvas.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(video, 0, 0, w, h);                 // live camera underneath
  let ts = performance.now();
  if (ts <= lastTs) ts = lastTs + 1;                // detectForVideo needs strictly increasing ts
  lastTs = ts;
  let res;
  try { res = vidLm.detectForVideo(video, ts); } catch(_){ return; }
  const faces = res && res.faceLandmarks;
  if (faces && faces.length){
    const dst = faces[0].map(p => [p.x * w, p.y * h]);
    for (let i = 0; i < tris.length; i += 3){
      const A = tris[i], B = tris[i+1], C = tris[i+2];
      warpTri(srcPts[A], srcPts[B], srcPts[C], dst[A], dst[B], dst[C]);
    }
  }
}

async function start(){
  await ensureReady();
  video  = document.getElementById("maskCam");
  canvas = document.getElementById("injectCanvas");
  ctx = canvas.getContext("2d");
  cam = await navigator.mediaDevices.getUserMedia({ audio: false,
    video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 }, frameRate: { ideal: 30, max: 30 } } });
  video.srcObject = cam;
  await video.play();
  canvas.width = video.videoWidth || 720;
  canvas.height = video.videoHeight || 1280;
  cancelAnimationFrame(raf); lastTs = 0; render();
  return canvas.captureStream(30);
}

function stop(){
  cancelAnimationFrame(raf); raf = 0;
  if (cam){ cam.getTracks().forEach(t => t.stop()); cam = null; }
  if (video) video.srcObject = null;
}

window.FaceMask = { start, stop, ensureReady };
