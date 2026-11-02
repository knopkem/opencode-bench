import "./styles.css";

const app = document.getElementById("app");

if (!app) {
  throw new Error("#app missing");
}

app.innerHTML = `
  <main class="placeholder">
    <h1>SpriteForge Lite</h1>
    <p>Implement the sprite editor here.</p>
  </main>
`;
