import { useState, memo, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage1.css';

const Stage1 = memo(function Stage1({ responses, pendingModels = 0 }) {
  const [activeTab, setActiveTab] = useState(0);

  // Memoize tab click handler
  const handleTabClick = useCallback((index) => {
    setActiveTab(index);
  }, []);

  // Show loading state if no responses yet but models are pending
  if (!responses || responses.length === 0) {
    if (pendingModels > 0) {
      return (
        <div className="stage stage1">
          <h3 className="stage-title">Stage 1: Individual Responses</h3>
          <div className="stage1-loading">
            <div className="spinner"></div>
            <p>Waiting for model responses... ({pendingModels} pending)</p>
          </div>
        </div>
      );
    }
    return null;
  }

  const activeResponse = responses[activeTab];
  const hasCodeExecution = activeResponse.code && activeResponse.execution_result;

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      {/* Progress indicator if some models still pending */}
      {pendingModels > 0 && (
        <div className="stage1-progress">
          <span className="progress-text">
            {responses.length} of {responses.length + pendingModels} models complete
          </span>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${(responses.length / (responses.length + pendingModels)) * 100}%` }}
            />
          </div>
        </div>
      )}

      <div className="tabs">
        {responses.map((resp, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''} ${resp.execution_result?.success === false ? 'error' : ''}`}
            onClick={() => handleTabClick(index)}
          >
            {resp.model.split('/')[1] || resp.model}
            {resp.code && (
              <span className={`code-indicator ${resp.execution_result?.success ? 'success' : 'error'}`}>
                {resp.execution_result?.success ? ' [code]' : ' [err]'}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="model-name">{activeResponse.model}</div>

        {hasCodeExecution ? (
          <div className="code-execution-block">
            <div className="code-section">
              <div className="code-header">Python Code:</div>
              <pre className="code-block">
                <code>{activeResponse.code}</code>
              </pre>
            </div>

            <div className="output-section">
              <div className="output-header">
                {activeResponse.execution_result?.success ? 'Output:' : 'Error:'}
              </div>
              <pre className={`output-block ${activeResponse.execution_result?.success ? '' : 'error'}`}>
                {activeResponse.execution_result?.success
                  ? activeResponse.execution_result.stdout || '(No output)'
                  : activeResponse.execution_result?.errors?.join('\n') || 'Unknown error'
                }
              </pre>

              {activeResponse.execution_result?.images?.map((img, i) => (
                <img
                  key={i}
                  src={`http://localhost:8001/outputs/${img.split('/').pop()}`}
                  alt={`Generated plot ${i + 1}`}
                  className="generated-plot"
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="response-text markdown-content">
            <ReactMarkdown>{activeResponse.response}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
});

export default Stage1;
