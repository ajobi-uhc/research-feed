import { useEffect } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import Feed from "./pages/Feed";
import DigestView from "./pages/DigestView";
import Profile from "./pages/Profile";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import Onboarding from "./pages/Onboarding";

export default function App() {
  const nav = useNavigate();
  const loc = useLocation();

  // First-time users (no profile yet) get sent to onboarding.
  useEffect(() => {
    if (loc.pathname === "/onboarding" || loc.pathname.startsWith("/runs")) return;
    api
      .getProfile()
      .then((p) => {
        if (!p) nav("/onboarding");
      })
      .catch(() => {});
  }, [loc.pathname]);

  return (
    <>
      <header>
        <div className="bar">
          <h1>
            <NavLink to="/">Safety Feed</NavLink>
          </h1>
          <nav>
            <NavLink to="/" end>Feed</NavLink>
            <NavLink to="/profile">Profile</NavLink>
            <NavLink to="/runs">Runs</NavLink>
          </nav>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Feed />} />
          <Route path="/digests/:id" element={<DigestView />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/onboarding" element={<Onboarding />} />
        </Routes>
      </main>
    </>
  );
}
