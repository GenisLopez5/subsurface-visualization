using System;
using System.Collections.Generic;

namespace Subsurface.Borehole
{
    /// <summary>Piecewise interpolation of original, top-origin document pixels. Never extrapolates.</summary>
    public sealed class BoreholeImageMap
    {
        public static int IntervalAtPixel(BoreholeDocument data,double resultPixel,double displacement)
        {
            if(data==null||!BoreholeCsv.Finite(resultPixel)||!BoreholeCsv.Finite(displacement))return -1;
            double pixel=resultPixel-displacement;
            for(int i=0;i<data.Intervals.Count;i++){var row=data.Intervals[i];if(!row.IsGap&&row.HasImageCoordinates&&pixel>=row.TopPixel&&pixel<row.BottomPixel)return i;}
            return -1;
        }
        readonly List<double> depths=new List<double>(),pixels=new List<double>();
        public double FirstPixel=>pixels[0];
        public double LastPixel=>pixels[pixels.Count-1];
        public static bool TryCreate(BoreholeDocument document,out BoreholeImageMap map,out string reason,double displacement=0)
        {
            map=null;reason=null;var candidate=new BoreholeImageMap();
            foreach(var row in document.Intervals)
            {
                if(row.IsGap)continue;
                if(!row.HasImageCoordinates){reason="Document sync needs valid top_y and bottom_y on every CSV row.";return false;}
                if(!candidate.Add(row.TopFeet,row.TopPixel+displacement)||!candidate.Add(row.BottomFeet,row.BottomPixel+displacement))
                {reason="Document sync paused: pixel coordinates overlap, reverse, or disagree at a shared depth.";return false;}
            }
            if(candidate.depths.Count<2){reason="No image coordinates are available.";return false;}
            map=candidate;return true;
        }
        bool Add(double depth,double pixel)
        {
            int n=depths.Count;
            if(n>0)
            {
                if(depth==depths[n-1])return Math.Abs(pixel-pixels[n-1])<.001;
                if(depth<depths[n-1]||pixel<=pixels[n-1])return false;
            }
            depths.Add(depth);pixels.Add(pixel);return true;
        }
        public double ToPixel(double feet)=>Interpolate(depths,pixels,feet);
        public double ToDepth(double pixel)=>Interpolate(pixels,depths,pixel);
        static double Interpolate(List<double> from,List<double> to,double value)
        {
            if(!BoreholeCsv.Finite(value))throw new ArgumentException("A finite coordinate is required.");
            if(value<=from[0])return to[0];int last=from.Count-1;if(value>=from[last])return to[last];
            int lo=0,hi=last;while(hi-lo>1){int mid=(lo+hi)/2;if(from[mid]<=value)lo=mid;else hi=mid;}
            return to[lo]+(value-from[lo])/(from[hi]-from[lo])*(to[hi]-to[lo]);
        }
    }
}
