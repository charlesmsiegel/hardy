// A failed render belongs to the page that failed, leaving the frame's
// navigation and Stop alive. Resetting retries that subtree; a new route or
// project retries automatically, without reloading the browser or session.
// Chat gets a separate instance in Shell so its mount survives other errors.
import {Component} from 'react';

export default class ErrorBoundary extends Component {
  state = {message: null};

  static getDerivedStateFromError(error) {
    return {message: String(error?.message ?? error)};
  }

  componentDidUpdate(previous) {
    if (previous.resetKey !== this.props.resetKey && this.state.message !== null) {
      this.setState({message: null});
    }
  }

  render() {
    if (this.state.message === null) return this.props.children;
    return (
      <>
        <div role="alert" className="panel__error">This page failed to draw: {this.state.message}</div>
        <button type="button" className="button" onClick={() => this.setState({message: null})}>
          Reload page
        </button>
      </>
    );
  }
}
