import { NavLink, Route, Routes } from "react-router-dom";
import useSWR from "swr";
import { api } from "./api.ts";
import Candidates from "./pages/Candidates.tsx";
import Explorer from "./pages/Explorer.tsx";
import Queue from "./pages/Queue.tsx";

export default function App() {
  const { data: stats } = useSWR("stats", api.stats, { refreshInterval: 8000 });

  return (
    <div className="app">
      <nav className="nav">
        <span className="nav-logo">xFriars Studio</span>
        <NavLink
          to="/"
          end
          className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
        >
          Candidates
          {stats?.new_candidates ? (
            <span className="nav-badge">{stats.new_candidates}</span>
          ) : null}
        </NavLink>
        <NavLink
          to="/queue"
          className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
        >
          Queue
          {stats?.queue_size ? (
            <span className="nav-badge">{stats.queue_size}</span>
          ) : null}
        </NavLink>
        <NavLink
          to="/explorer"
          className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
        >
          Explorer
        </NavLink>
      </nav>
      <div className="content">
        <Routes>
          <Route path="/" element={<Candidates />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/explorer" element={<Explorer />} />
        </Routes>
      </div>
    </div>
  );
}
