import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <main className="page page--narrow">
      <h1>Not found</h1>
      <p>
        There is nothing at this address. <Link to="/">Start again</Link>.
      </p>
    </main>
  );
}
