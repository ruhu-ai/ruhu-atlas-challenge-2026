/**
 * Template Gallery Component
 *
 * Displays a browseable gallery of agent templates with filtering,
 * search, and preview capabilities.
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Search,
  Filter,
  Star,
  Loader2,
  Mic,
  MessageSquare,
  Layers,
} from 'lucide-react';
import { Button } from '@/components/atoms/button';
import { Input } from '@/components/atoms/input';
import { Badge } from '@/components/atoms/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import { cn } from '@/lib/utils';
import {
  agentTemplateService,
  type AgentTemplate,
  type CloneAgentTemplateResponse,
} from '@/api/services/template.service';
import { TemplateCard } from './TemplateCard';
import { TemplateDetailModal } from './TemplateDetailModal';

export interface TemplateGalleryProps {
  onSelectTemplate?: (template: AgentTemplate) => void;
  onTemplateCloned?: (agentId: string) => void;
  showCloneButton?: boolean;
}

export function TemplateGallery({
  onSelectTemplate,
  onTemplateCloned,
  showCloneButton = true,
}: TemplateGalleryProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Data
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [filteredTemplates, setFilteredTemplates] = useState<AgentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<AgentTemplate | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [agentTypeFilter, setAgentTypeFilter] = useState<string>('all');
  const [showFeaturedOnly, setShowFeaturedOnly] = useState(false);

  useEffect(() => { loadTemplates(); }, []);

  useEffect(() => { applyFilters(); }, [templates, searchQuery, categoryFilter, agentTypeFilter, showFeaturedOnly]);

  const loadTemplates = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await agentTemplateService.listTemplates({ page_size: 50 });
      setTemplates(response.templates ?? []);
    } catch (err) {
      console.error('Failed to load templates:', err);
      setError('Failed to load templates. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const applyFilters = () => {
    let filtered = [...templates];

    if (showFeaturedOnly) {
      filtered = filtered.filter((t) => t.is_featured);
    }
    if (categoryFilter !== 'all') {
      filtered = filtered.filter((t) => t.category === categoryFilter);
    }
    if (agentTypeFilter !== 'all') {
      filtered = filtered.filter((t) => t.default_agent_settings.agent_type === agentTypeFilter);
    }
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (t) =>
          t.name.toLowerCase().includes(query) ||
          t.description.toLowerCase().includes(query) ||
          t.tags.some((tag) => tag.toLowerCase().includes(query)),
      );
    }

    setFilteredTemplates(filtered);
  };

  const handleUseTemplate = async (template: AgentTemplate, cloneResponse?: CloneAgentTemplateResponse) => {
    if (cloneResponse?.agent_id) {
      // Ensure the agent list is fresh before navigating to the new agent canvas
      await queryClient.invalidateQueries({ queryKey: ['agents-list'] });
      if (onTemplateCloned) {
        onTemplateCloned(cloneResponse.agent_id);
      } else {
        // Route to /setup with template provenance carried via query
        // string. The setup page renders the required-tools checklist
        // and gates "Continue to canvas" on satisfaction. Templates
        // with no required external tools fall through immediately.
        const hasRequiredTools = (template.required_tools?.length ?? 0) > 0;
        if (hasRequiredTools) {
          navigate(
            `/agents/${cloneResponse.agent_id}/setup?template=${cloneResponse.template_id}`,
          );
        } else {
          navigate(`/agents/${cloneResponse.agent_id}/canvas`);
        }
      }
      setSelectedTemplate(null);
      return;
    }
    if (onSelectTemplate) {
      onSelectTemplate(template);
      setSelectedTemplate(null);
      return;
    }
    navigate(`/agents/new/canvas?template=${template.template_id}`);
    setSelectedTemplate(null);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="ml-2 text-gray-600">Loading templates…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-64">
        <p className="text-red-600 mb-4">{error}</p>
        <Button onClick={loadTemplates}>Retry</Button>
      </div>
    );
  }

  const categories = Array.from(new Set(templates.map((t) => t.category))).sort();

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Agent Templates</h2>
          <p className="text-gray-600 mt-1">
            Start with a pre-built agent and customise it to your needs
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => setShowFeaturedOnly(!showFeaturedOnly)}
          className={cn(showFeaturedOnly && 'bg-yellow-50 border-yellow-300')}
        >
          <Star className={cn('h-4 w-4 mr-2', showFeaturedOnly && 'fill-yellow-400 text-yellow-400')} />
          Featured
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-4 flex-wrap">
        {/* Search */}
        <div className="flex-1 min-w-[200px]">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <Input
              type="text"
              placeholder="Search templates…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>

        {/* Category */}
        <Select value={categoryFilter} onValueChange={setCategoryFilter}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="All Categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Categories</SelectItem>
            {categories.map((category) => (
              <SelectItem key={category} value={category}>
                {category.charAt(0).toUpperCase() + category.slice(1).replace(/-/g, ' ')}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Agent type */}
        <Select value={agentTypeFilter} onValueChange={setAgentTypeFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="All Types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="voice">
              <span className="flex items-center gap-2">
                <Mic className="h-3.5 w-3.5" /> Voice
              </span>
            </SelectItem>
            <SelectItem value="chat">
              <span className="flex items-center gap-2">
                <MessageSquare className="h-3.5 w-3.5" /> Chat
              </span>
            </SelectItem>
            <SelectItem value="multimodal">
              <span className="flex items-center gap-2">
                <Layers className="h-3.5 w-3.5" /> Multimodal
              </span>
            </SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Count */}
      <div className="flex items-center justify-between text-sm text-gray-600">
        <span>
          Showing {filteredTemplates.length}{' '}
          {filteredTemplates.length === 1 ? 'template' : 'templates'}
        </span>
        {(categoryFilter !== 'all' || agentTypeFilter !== 'all' || searchQuery || showFeaturedOnly) && (
          <Badge variant="secondary" className="text-xs">Filtered</Badge>
        )}
      </div>

      {/* Grid */}
      {filteredTemplates.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12">
          <Filter className="h-12 w-12 text-gray-300 mb-3" />
          <p className="text-gray-500">No templates match your filters</p>
          <Button
            variant="link"
            onClick={() => {
              setSearchQuery('');
              setCategoryFilter('all');
              setAgentTypeFilter('all');
              setShowFeaturedOnly(false);
            }}
            className="mt-2"
          >
            Clear filters
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredTemplates.map((template) => (
            <TemplateCard
              key={template.template_id}
              template={template}
              onClick={() => setSelectedTemplate(template)}
            />
          ))}
        </div>
      )}

      {/* Detail modal */}
      {selectedTemplate && (
        <TemplateDetailModal
          template={selectedTemplate}
          onClose={() => setSelectedTemplate(null)}
          onUseTemplate={handleUseTemplate}
          showCloneButton={showCloneButton}
        />
      )}
    </div>
  );
}
