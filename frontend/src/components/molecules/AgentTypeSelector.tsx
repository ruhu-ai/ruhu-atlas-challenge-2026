/**
 * AgentTypeSelector Component
 *
 * Modal for selecting agent type when creating a new agent.
 * Supports three modalities: Chat, Voice, and Multi-modal.
 * Accepts optional training document uploads — files are ingested into the
 * knowledge base and the resulting article IDs are forwarded to the canvas.
 */

import React, { useState, useRef } from 'react';
import { MessageSquare, Mic, Layers, Upload, FileText, X, Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/atoms/dialog';
import { Button } from '@/components/atoms/button';
import type { AgentType } from '@/types/agent';
import { knowledgeBaseService } from '@/api/services/knowledge-base.service';
import { createLogger } from '@/utils/logger';

const selectorLogger = createLogger({ prefix: '[AgentTypeSelector]' });

const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.txt'];
const ACCEPTED_MIME = 'application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain';

interface AgentTypeSelectorProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (type: AgentType, documentIds?: string[]) => void;
}

interface AgentTypeOption {
  type: AgentType;
  name: string;
  icon: React.ElementType;
  description: string;
  channels: string[];
}

const agentTypes: AgentTypeOption[] = [
  {
    type: 'chat',
    name: 'Chat',
    icon: MessageSquare,
    description: 'Text-only agent for web chat, WhatsApp, and SMS',
    channels: ['Web Chat', 'WhatsApp', 'SMS'],
  },
  {
    type: 'voice',
    name: 'Voice',
    icon: Mic,
    description: 'Voice-only agent for phone calls',
    channels: ['Phone', 'WebRTC'],
  },
  {
    type: 'multimodal',
    name: 'Multi-modal',
    icon: Layers,
    description: 'Unified agent handling both voice AND chat with seamless switching',
    channels: ['Phone', 'Web Chat', 'WhatsApp', 'SMS'],
  },
];

export const AgentTypeSelector: React.FC<AgentTypeSelectorProps> = ({
  open,
  onOpenChange,
  onSelect,
}) => {
  const [selectedType, setSelectedType] = useState<AgentType>('chat');
  const [isDragging, setIsDragging] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addFiles = (incoming: File[]) => {
    setUploadError(null);
    const valid = incoming.filter((f) => {
      const ext = '.' + f.name.split('.').pop()?.toLowerCase()
      return ACCEPTED_EXTENSIONS.includes(ext)
    })
    const invalid = incoming.length - valid.length
    if (invalid > 0) {
      setUploadError(`${invalid} file(s) skipped — only PDF, DOCX, and TXT are supported.`)
    }
    setPendingFiles((prev) => {
      const existingNames = new Set(prev.map((f) => f.name))
      return [...prev, ...valid.filter((f) => !existingNames.has(f.name))]
    })
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    addFiles(Array.from(e.dataTransfer.files));
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      addFiles(Array.from(e.target.files));
      // Reset so the same file can be re-selected after removal
      e.target.value = '';
    }
  };

  const removeFile = (name: string) => {
    setPendingFiles((prev) => prev.filter((f) => f.name !== name));
  };

  const handleContinue = async () => {
    if (pendingFiles.length === 0) {
      onSelect(selectedType);
      onOpenChange(false);
      return;
    }

    setIsUploading(true);
    setUploadError(null);

    const articleIds: string[] = [];
    const failed: string[] = [];

    for (const file of pendingFiles) {
      try {
        const doc = await knowledgeBaseService.uploadDocument(file, {
          category: 'training',
          status: 'published',
        });
        articleIds.push(doc.document_id);
        selectorLogger.debug('Uploaded document', { id: doc.document_id, name: file.name });
      } catch (err) {
        selectorLogger.error('Upload failed', { name: file.name, err });
        failed.push(file.name);
      }
    }

    setIsUploading(false);

    if (failed.length > 0) {
      setUploadError(`Failed to upload: ${failed.join(', ')}. You can still continue or retry.`);
    }

    onSelect(selectedType, articleIds.length > 0 ? articleIds : undefined);
    onOpenChange(false);
  };

  const selectedOption = agentTypes.find((t) => t.type === selectedType)!;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl bg-card border-border text-foreground">
        <DialogHeader className="text-center pb-2">
          <div className="flex justify-center mb-3">
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-pink-400 via-purple-400 to-blue-400" />
          </div>
          <DialogTitle className="text-xl font-semibold text-foreground">
            Create new agent
          </DialogTitle>
          <DialogDescription className="text-muted-foreground">
            Select the type of agent you want to create
          </DialogDescription>
        </DialogHeader>

        {/* Agent Type Tabs */}
        <div className="flex rounded-lg bg-muted p-1 mb-6">
          {agentTypes.map((option) => {
            const Icon = option.icon;
            const isSelected = selectedType === option.type;
            return (
              <button
                key={option.type}
                onClick={() => setSelectedType(option.type)}
                className={`
                  flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-md text-sm font-medium transition-all
                  ${isSelected
                    ? 'bg-accent text-foreground shadow-lg'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }
                `}
              >
                <Icon className="h-4 w-4" />
                {option.name}
              </button>
            );
          })}
        </div>

        {/* Selected Type Details */}
        <div className="mb-6 p-4 rounded-lg bg-muted border border-border">
          <p className="text-sm text-foreground/70 mb-3">{selectedOption.description}</p>
          <div className="flex flex-wrap gap-2">
            {selectedOption.channels.map((channel) => (
              <span
                key={channel}
                className="px-2 py-1 text-xs rounded-full bg-muted text-foreground/70"
              >
                {channel}
              </span>
            ))}
          </div>
        </div>

        {/* Training Documents Section */}
        <div className="space-y-3">
          <div>
            <h3 className="text-sm font-medium text-foreground mb-1">Add training documents</h3>
            <p className="text-xs text-muted-foreground">
              Attach files to give your agent business context
            </p>
          </div>

          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_MIME}
            multiple
            className="hidden"
            onChange={handleFileInputChange}
          />

          {/* File Drop Zone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`
              border-2 border-dashed rounded-lg p-8 text-center transition-all cursor-pointer
              ${isDragging
                ? 'border-primary bg-primary/10'
                : 'border-border hover:border-border hover:bg-muted'
              }
            `}
          >
            <div className="flex justify-center mb-3">
              <div className="p-2 rounded-lg bg-muted">
                <Upload className="h-4 w-4 text-muted-foreground" />
              </div>
            </div>
            <p className="text-sm text-muted-foreground">
              Drag files here or click to browse
            </p>
            <p className="text-xs text-muted-foreground/70 mt-1">
              PDF, DOCX, or TXT · max 10 MB each
            </p>
          </div>

          {/* Queued files */}
          {pendingFiles.length > 0 && (
            <ul className="space-y-1">
              {pendingFiles.map((file) => (
                <li
                  key={file.name}
                  className="flex items-center gap-2 text-sm px-3 py-2 rounded-md bg-muted"
                >
                  <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                  <span className="flex-1 truncate text-foreground/80">{file.name}</span>
                  <span className="text-xs text-muted-foreground shrink-0">
                    {(file.size / 1024).toFixed(0)} KB
                  </span>
                  <button
                    onClick={(e) => { e.stopPropagation(); removeFile(file.name); }}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                    aria-label={`Remove ${file.name}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {uploadError && (
            <p className="text-xs text-destructive">{uploadError}</p>
          )}
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-border">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={isUploading}
            className="text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            Cancel
          </Button>
          <Button
            onClick={handleContinue}
            disabled={isUploading}
            className="bg-primary hover:bg-primary/90 text-primary-foreground px-6"
          >
            {isUploading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Uploading…
              </>
            ) : (
              'Continue'
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default AgentTypeSelector;
