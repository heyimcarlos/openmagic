import { FileIcon } from 'lucide-react';
import ReactMarkdown, { type Components, type ExtraProps } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import {
  Attachment,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
  AttachmentTrigger,
} from '@/components/ui/attachment';

const ATTACHMENT_EXTENSIONS = new Set([
  'csv', 'doc', 'docx', 'gif', 'gz', 'jpeg', 'jpg', 'json', 'md', 'mov', 'mp3', 'mp4',
  'pdf', 'png', 'ppt', 'pptx', 'rtf', 'svg', 'tar', 'txt', 'webp', 'xls', 'xlsx', 'zip',
]);
const FILE_EXTENSION = /\.([a-z0-9]{1,8})(?:$|[?#])/i;

interface StandaloneAttachment {
  extension: string;
  href: string;
  label: string;
}

function extractStandaloneAttachment(node: ExtraProps['node']): StandaloneAttachment | null {
  const onlyChild = node?.children.length === 1 ? node.children[0] : null;
  const href =
    onlyChild?.type === 'element' &&
    onlyChild.tagName === 'a' &&
    typeof onlyChild.properties.href === 'string'
      ? onlyChild.properties.href
      : null;
  const extension = href?.match(FILE_EXTENSION)?.[1]?.toLowerCase() ?? null;

  if (!href || !extension || !ATTACHMENT_EXTENSIONS.has(extension) || onlyChild?.type !== 'element') {
    return null;
  }

  const label = onlyChild.children
    .filter((child) => child.type === 'text')
    .map((child) => child.value)
    .join('') || href;

  return { extension: extension.toUpperCase(), href, label };
}

const markdownComponents: Components = {
  a: ({ href = '', children, ...props }) => (
    <a href={href} target="_blank" rel="noreferrer" {...props}>
      {children}
    </a>
  ),
  p: ({ node, children }) => {
    const attachment = extractStandaloneAttachment(node);

    if (attachment) {
      return (
        <Attachment className="my-4">
          <AttachmentMedia>
            <FileIcon />
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle>{attachment.label}</AttachmentTitle>
            <AttachmentDescription>{attachment.extension}</AttachmentDescription>
          </AttachmentContent>
          <AttachmentTrigger asChild>
            <a href={attachment.href} target="_blank" rel="noreferrer" aria-label={`Open ${attachment.label}`} />
          </AttachmentTrigger>
        </Attachment>
      );
    }

    return <p>{children}</p>;
  },
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="chat-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
