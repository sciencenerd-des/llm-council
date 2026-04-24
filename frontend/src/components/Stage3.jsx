import { memo, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage3.css';

/**
 * Custom component to render the Chairman's report with embedded visualizations.
 * Handles [[IMAGE_REF:id:model]] markers in the text.
 */
const ReportWithVisualizations = memo(function ReportWithVisualizations({ content, referencedImages }) {
  // Parse the content and split by image reference markers
  const parts = useMemo(() => {
    if (!content) return [];

    const imageRefPattern = /\[\[IMAGE_REF:(\d+):([^\]]+)\]\]/g;
    const result = [];
    let lastIndex = 0;
    let match;

    while ((match = imageRefPattern.exec(content)) !== null) {
      // Add text before the marker
      if (match.index > lastIndex) {
        result.push({
          type: 'text',
          content: content.slice(lastIndex, match.index)
        });
      }

      // Add the image reference
      const refId = parseInt(match[1], 10);
      const modelName = match[2];
      const imageInfo = referencedImages?.find(img => img.ref_id === refId);

      if (imageInfo) {
        result.push({
          type: 'image',
          refId,
          modelName,
          path: imageInfo.path
        });
      } else {
        // Fallback if image not found
        result.push({
          type: 'text',
          content: `*(Visualization from ${modelName} not available)*`
        });
      }

      lastIndex = match.index + match[0].length;
    }

    // Add remaining text after last marker
    if (lastIndex < content.length) {
      result.push({
        type: 'text',
        content: content.slice(lastIndex)
      });
    }

    return result;
  }, [content, referencedImages]);

  return (
    <div className="report-content">
      {parts.map((part, index) => {
        if (part.type === 'text') {
          return (
            <div key={index} className="markdown-content">
              <ReactMarkdown>{part.content}</ReactMarkdown>
            </div>
          );
        } else if (part.type === 'image') {
          return (
            <figure key={index} className="report-visualization">
              <img
                src={`http://localhost:8001/outputs/${part.path.split('/').pop()}`}
                alt={`Visualization from ${part.modelName}`}
                className="report-viz-image"
              />
              <figcaption className="viz-caption">
                Source: {part.modelName}
              </figcaption>
            </figure>
          );
        }
        return null;
      })}
    </div>
  );
});

const Stage3 = memo(function Stage3({ finalResponse }) {
  if (!finalResponse) {
    return null;
  }

  // Determine if this is the new report format or legacy code execution
  const isReport = finalResponse.is_report === true;
  const referencedImages = finalResponse.images || [];
  const hasVisualizations = referencedImages.length > 0;

  // Memoize model short name extraction
  const modelShortName = useMemo(() => {
    return finalResponse.model.split('/')[1] || finalResponse.model;
  }, [finalResponse.model]);

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Chairman's Research Report</h3>
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {modelShortName}
          {isReport && hasVisualizations && (
            <span className="synthesis-badge">
              Synthesized report with {referencedImages.length} visualization{referencedImages.length > 1 ? 's' : ''}
            </span>
          )}
        </div>

        {isReport ? (
          /* New report format with embedded visualizations */
          <ReportWithVisualizations
            content={finalResponse.response}
            referencedImages={referencedImages}
          />
        ) : (
          /* Legacy format - plain text response */
          <div className="final-text markdown-content">
            <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
          </div>
        )}

        {/* Show all referenced visualizations at the bottom as a gallery (optional) */}
        {isReport && hasVisualizations && (
          <div className="visualizations-gallery">
            <h4 className="gallery-title">All Referenced Visualizations</h4>
            <div className="gallery-grid">
              {referencedImages.map((img, i) => (
                <div key={i} className="gallery-item">
                  <img
                    src={`http://localhost:8001/outputs/${img.path.split('/').pop()}`}
                    alt={`Visualization ${i + 1} from ${img.model}`}
                    className="gallery-image"
                  />
                  <div className="gallery-caption">
                    {img.model} (Figure {img.index + 1})
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
});

export default Stage3;
