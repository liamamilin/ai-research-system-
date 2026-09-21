import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    console.error("[ui] unhandled render error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen items-center justify-center bg-bg p-6">
          <div className="card p-6 max-w-lg w-full space-y-3 text-center">
            <div className="text-lg font-semibold text-danger">页面运行出错</div>
            <div className="text-xs text-text-muted font-mono break-all">
              {this.state.error.message}
            </div>
            <div className="flex gap-2 justify-center">
              <button
                className="btn btn-primary text-xs"
                onClick={() => window.location.reload()}
              >
                刷新页面
              </button>
              <button
                className="btn text-xs"
                onClick={() => this.setState({ error: null })}
              >
                重试
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
