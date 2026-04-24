import { useState, memo, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage2.css';

// Memoize text transformation to avoid recomputation
function deAnonymizeText(text, labelToModel) {
  if (!labelToModel) return text;

  let result = text;
  // Replace each "Response X" with the actual model name
  Object.entries(labelToModel).forEach(([label, model]) => {
    const modelShortName = model.split('/')[1] || model;
    result = result.replace(new RegExp(label, 'g'), `**${modelShortName}**`);
  });
  return result;
}

const Stage2 = memo(function Stage2({ rankings, labelToModel, aggregateRankings, pendingModels = 0 }) {
  const [activeTab, setActiveTab] = useState(0);
  const [showCode, setShowCode] = useState(false);

  // Memoize tab click handler
  const handleTabClick = useCallback((index) => {
    setActiveTab(index);
    setShowCode(false);  // Reset code view when switching tabs
  }, []);

  // Memoize de-anonymized text for current tab
  const deAnonymizedText = useMemo(() => {
    if (!rankings || rankings.length === 0) return '';
    return deAnonymizeText(rankings[activeTab]?.ranking || '', labelToModel);
  }, [rankings, activeTab, labelToModel]);

  // Show loading state if no rankings yet but models are pending
  if (!rankings || rankings.length === 0) {
    if (pendingModels > 0) {
      return (
        <div className="stage stage2">
          <h3 className="stage-title">Stage 2: Peer Rankings</h3>
          <div className="stage2-loading">
            <div className="spinner"></div>
            <p>Models are evaluating responses... ({pendingModels} pending)</p>
          </div>
        </div>
      );
    }
    return null;
  }

  const activeRanking = rankings[activeTab];
  const hasVerificationCode = activeRanking?.verification_code && activeRanking?.execution_result;

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>

      {/* Progress indicator if some models still pending */}
      {pendingModels > 0 && (
        <div className="stage2-progress">
          <span className="progress-text">
            {rankings.length} of {rankings.length + pendingModels} evaluations complete
          </span>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${(rankings.length / (rankings.length + pendingModels)) * 100}%` }}
            />
          </div>
        </div>
      )}

      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
        {hasVerificationCode && " Some models ran verification code to validate claims."}
      </p>

      <div className="tabs">
        {rankings.map((rank, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''} ${rank.execution_result ? 'has-code' : ''}`}
            onClick={() => handleTabClick(index)}
          >
            {rank.model.split('/')[1] || rank.model}
            {rank.execution_result && (
              <span className={`code-indicator ${rank.execution_result.success ? 'success' : 'error'}`}>
                {rank.execution_result.success ? ' [verified]' : ' [code err]'}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="ranking-model">
          {activeRanking.model}
        </div>
        <div className="ranking-content markdown-content">
          <ReactMarkdown>
            {deAnonymizedText}
          </ReactMarkdown>
        </div>

        {/* Verification Code Section */}
        {hasVerificationCode && (
          <div className="verification-section">
            <button
              className="toggle-code-btn"
              onClick={() => setShowCode(!showCode)}
            >
              {showCode ? 'Hide' : 'Show'} Verification Code
              {activeRanking.execution_result.success ? ' (Success)' : ' (Failed)'}
            </button>

            {showCode && (
              <div className="verification-code-block">
                <div className="code-header">Verification Code:</div>
                <pre className="code-block">
                  <code>{activeRanking.verification_code}</code>
                </pre>

                <div className="output-header">
                  {activeRanking.execution_result.success ? 'Output:' : 'Error:'}
                </div>
                <pre className={`output-block ${activeRanking.execution_result.success ? '' : 'error'}`}>
                  {activeRanking.execution_result.success
                    ? activeRanking.execution_result.stdout || '(No output)'
                    : activeRanking.execution_result.errors?.join('\n') || 'Unknown error'
                  }
                </pre>

                {activeRanking.execution_result.images?.map((img, i) => (
                  <img
                    key={i}
                    src={`http://localhost:8001/outputs/${img.split('/').pop()}`}
                    alt={`Verification plot ${i + 1}`}
                    className="verification-plot"
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Parsed Ranking */}
        {activeRanking.parsed_ranking && activeRanking.parsed_ranking.length > 0 && (
          <div className="parsed-ranking">
            <strong>Extracted Ranking:</strong>
            <ol>
              {activeRanking.parsed_ranking.map((label, i) => (
                <li key={i}>
                  {labelToModel && labelToModel[label]
                    ? labelToModel[label].split('/')[1] || labelToModel[label]
                    : label}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      {/* Aggregate Rankings */}
      {aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>Aggregate Rankings (Street Cred)</h4>
          <p className="stage-description">
            Combined results across all peer evaluations (lower score is better):
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => (
              <div key={index} className="aggregate-item">
                <span className="rank-position">#{index + 1}</span>
                <span className="rank-model">
                  {agg.model.split('/')[1] || agg.model}
                </span>
                <span className="rank-score">
                  Avg: {agg.average_rank.toFixed(2)}
                </span>
                <span className="rank-count">
                  ({agg.rankings_count} votes)
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});

export default Stage2;
