/* MIT. Read-only framebuffer geometry query; renders an 8-bit grayscale PNG.
 * Embedded Noto Sans SC glyph subset (OFL), stored DEFLATE blocks; no runtime font dependency. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <ctype.h>
#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#endif
#include "screen_glyphs.h"
/* Keep the shell's ASCII messages as a fallback for legacy eips text. */
static const char *localized(const char *s,char *buf,size_t size){
 const char *en[]={"KC: Starting...","KC: Starting refresh","KC: Checking edit ability","KC: Checking lock","KC: Checking tasks","KC: Backing up / checking","KC: Refreshing snapshot","KC: Snapshot failed. See PC","KC: Stopped. Check PC/log","KC: Snapshot ready","KC: Test passed (6/6)","KC: Test NOT confirmed","KC: No new tasks"};
 const char *zh[]={"正在启动","正在准备刷新","正在验证编辑能力","正在检查运行状态","正在检查任务","正在备份与校验","正在刷新收藏夹","刷新失败，请连接电脑查看","执行中断，请连接电脑查看","收藏夹状态已刷新","编辑能力验证通过（6/6）","编辑能力尚未验证通过","没有新的待执行任务"};
 for(size_t i=0;i<sizeof(en)/sizeof(en[0]);i++)if(!strcmp(s,en[i]))return zh[i];
 unsigned a,b;
 if(sscanf(s,"KC: Op %u/%u",&a,&b)==2){snprintf(buf,size,"正在执行：%u/%u",a,b);return buf;}
 if(sscanf(s,"KC: Test step %u/%u",&a,&b)==2){snprintf(buf,size,"正在验证编辑能力：%u/%u",a,b);return buf;}
 if(sscanf(s,"KC: OK %u Review %u",&a,&b)==2){snprintf(buf,size,"已确认 %u 项，待核对 %u 项",a,b);return buf;}
 if(sscanf(s,"KC: Done. OK %u",&a)==1){snprintf(buf,size,"已完成：确认 %u 项",a);return buf;}
 return s;
}
static unsigned decode(const unsigned char **s){
 unsigned c=*(*s)++;if(c<128)return c;
 if(c>=0xc2&&c<=0xdf&&((*s)[0]&0xc0)==0x80){unsigned n=*(*s)++;return ((c&31)<<6)|(n&63);}
 if(c>=0xe0&&c<=0xef&&(*s)[0]&&(*s)[1]&&((*s)[0]&0xc0)==0x80&&((*s)[1]&0xc0)==0x80){unsigned a=*(*s)++,b=*(*s)++;return ((c&15)<<12)|((a&63)<<6)|(b&63);}
 return '?';
}
static int glyph(unsigned cp){
 int lo=0,hi=(int)(sizeof(screen_codes)/sizeof(screen_codes[0]));
 while(lo<hi){int m=(lo+hi)/2;if(screen_codes[m]<cp)lo=m+1;else hi=m;}
 return lo<(int)(sizeof(screen_codes)/sizeof(screen_codes[0]))&&screen_codes[lo]==cp?lo:glyph('?');
}
static void be32(unsigned char *p,uint32_t n){p[0]=n>>24;p[1]=n>>16;p[2]=n>>8;p[3]=n;}
static uint32_t crc(uint32_t c,const unsigned char *p,size_t n){
 while(n--){c^=*p++;for(int i=0;i<8;i++)c=(c>>1)^((c&1)?0xedb88320:0);}return c;
}
static int chunk(FILE *f,const char *type,const unsigned char *p,size_t n){
 unsigned char b[4];be32(b,(uint32_t)n);if(fwrite(b,1,4,f)!=4||fwrite(type,1,4,f)!=4||fwrite(p,1,n,f)!=n)return 0;
 be32(b,~crc(crc(0xffffffff,(const unsigned char*)type,4),p,n));return fwrite(b,1,4,f)==4;
}
static void text(unsigned char *img,int w,int h,const unsigned *s,int count,int y,int px){
 int width=0;for(int i=0;i<count;i++)width+=s[i]<128?px/2:px;
 int x=(w-width)/2;
 for(int i=0;i<count;i++){
  int idx=glyph(s[i]),cw=s[i]<128?px/2:px,srcw=s[i]<128?16:32;
  for(int yy=0;yy<px;yy++)for(int xx=0;xx<cw;xx++){
   int bit=(yy*32/px)*32+xx*srcw/cw;unsigned char v=screen_bits[idx][bit/2];
   v=((bit&1)?(v&15):(v>>4))*17;
   if(x+xx>=0&&x+xx<w&&y+yy>=0&&y+yy<h)img[(w+1)*(y+yy)+1+x+xx]=v;
  }
  x+=cw;
 }
}
int main(int argc,char **argv){
 unsigned int sw=0,sh=0;
 if(argc==5){sw=(unsigned int)strtoul(argv[3],0,10);sh=(unsigned int)strtoul(argv[4],0,10);}
 else if(argc==3){
#ifndef _WIN32
 /* Linux fb_var_screeninfo starts with xres,yres; query only, no display ioctl. */
 unsigned int v[64]={0};int fd=open("/dev/fb0",O_RDONLY);if(fd<0)return 2;
 int rc=ioctl(fd,0x4600,v);close(fd);if(rc<0)return 2;sw=v[0];sh=v[1];
#else
 return 2;
#endif
 }else return 2;
 if(sw<320||sw>4096||sh<320||sh>4096||strlen(argv[2])>96)return 2;
 int w=(int)sw*9/10,px=w/22;if(px<18)px=18;if(px>80)px=80;if(px>(int)sh/5)px=(int)sh/5;
 int h=5*px,pad=w+1,pos=1,counts[3]={0},width=0;unsigned lines[3][128];char translated[256];
 const unsigned char *s=(const unsigned char*)localized(argv[2],translated,sizeof(translated));
 while(*s){unsigned cp=decode(&s);int cw=cp<128?px/2:px;
  if(width+cw>w-2*px){pos++;width=0;}if(pos>3||counts[pos-1]>=128)return 2;
  lines[pos-1][counts[pos-1]++]=cp;width+=cw;
 }
 size_t n=(size_t)pad*h;unsigned char *img=malloc(n),*z=malloc(n+n/65535*5+16);if(!img||!z){free(img);free(z);return 3;}
 memset(img,255,n);for(int y=0;y<h;y++){img[y*pad]=0;for(int x=0;x<w;x++)if(x<2||x>=w-2||y<2||y>=h-2)img[y*pad+1+x]=80;}
 int line_height=px+px/4,start=(h-((pos-1)*line_height+px))/2;
 for(int i=0;i<pos;i++)text(img,w,h,lines[i],counts[i],start+i*line_height,px);
 size_t off=0,k=2;z[0]=0x78;z[1]=0x01;uint32_t a=1,b=0;
 while(off<n){unsigned len=(unsigned)((n-off)>65535?65535:n-off);z[k++]=(off+len==n);z[k++]=len;z[k++]=len>>8;z[k++]=~len;z[k++]=(~len)>>8;memcpy(z+k,img+off,len);k+=len;off+=len;}
 for(size_t i=0;i<n;i++){a=(a+img[i])%65521;b=(b+a)%65521;}be32(z+k,(b<<16)|a);k+=4;
 unsigned char header[13]={0};be32(header,w);be32(header+4,h);header[8]=8;
 FILE *f=fopen(argv[1],"wb");int ok=0;if(f){ok=fwrite("\211PNG\r\n\032\n",1,8,f)==8&&chunk(f,"IHDR",header,13)&&chunk(f,"IDAT",z,k)&&chunk(f,"IEND",header,0);if(fclose(f))ok=0;}
 free(img);free(z);if(!ok)return 3;
 printf("%u %u\n",(sw-w)/2,(sh-h)/2);return 0;
}
